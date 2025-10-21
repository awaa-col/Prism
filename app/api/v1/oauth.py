"""
Unified OAuth endpoints: /auth/oauth/{provider}/authorize and /auth/oauth/{provider}/callback
Example provider: github
"""
from typing import Optional, Dict, Any
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
import secrets
from sqlalchemy.exc import IntegrityError

from app.core.cache import get_cache
from app.core.config import get_settings
from app.core.oauth_registry import get_oauth_provider_registry
from app.core.security import create_access_token, create_refresh_token_object
from app.db.session import get_db, AsyncSession
from app.db.models import User
from sqlalchemy import select
from app.core.structured_logging import get_logger


logger = get_logger(__name__)


router = APIRouter()


class OAuthAuthorizeResponse(BaseModel):
    redirect_to: str


class OAuthExchangeResponse(BaseModel):
    access_token: str
    refresh_token: str


class TicketExchangeRequest(BaseModel):
    ticket: str





@router.get("/auth/oauth/{provider}/authorize")
async def oauth_authorize(
    provider: str,
    request: Request,
):
    """Initiate OAuth flow by redirecting to provider authorize URL"""
    registry = get_oauth_provider_registry()
    p = registry.get_provider(provider)
    if not p:
        raise HTTPException(status_code=404, detail=f"Unknown or unconfigured provider: {provider}")

    settings = get_settings()
    cache = get_cache(prefix="oauth")

    # CSRF state
    state = secrets.token_urlsafe(24)
    # optional: PKCE
    code_verifier = secrets.token_urlsafe(64)
    code_challenge = p.build_pkce_challenge(code_verifier)

    # Persist state -> tie to client IP/UA to reduce risk
    await cache.set(
        f"state:{state}",
        {
            "provider": provider,
            "ip": request.client.host if request.client else None,
            "ua": request.headers.get("user-agent"),
            "code_verifier": code_verifier,
        },
        expire=300,
    )

    authorize_url = p.build_authorize_url(state=state, code_challenge=code_challenge)
    # Redirect user agent directly to provider authorization endpoint
    return RedirectResponse(url=authorize_url)


@router.get("/auth/oauth/{provider}/callback")
async def oauth_callback(
    provider: str,
    request: Request,
    code: Optional[str] = None,
    state: Optional[str] = None,
    error: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
):
    """Handle provider callback, exchange code for token, create/login local user, return JWT."""
    if error:
        logger.warning("OAuth provider returned an error", provider=provider, error=error)
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"OAuth error: {error}")
    if not code or not state:
        logger.warning("Missing code or state in OAuth callback", provider=provider)
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Missing code or state")

    cache = get_cache(prefix="oauth")
    state_data = await cache.get(f"state:{state}")
    if not state_data:
        logger.warning("Invalid or expired state received in OAuth callback", state=state)
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid or expired state")

    # Optional: validate IP/UA soft binding
    # (do not hard fail if different due to proxies)

    registry = get_oauth_provider_registry()
    p = registry.get_provider(provider)
    if not p:
        logger.error("Unknown or unconfigured provider requested", provider=provider)
        raise HTTPException(status_code=404, detail=f"Unknown or unconfigured provider: {provider}")
    
    try:
        token_data = await p.exchange_code(code, state_data.get("code_verifier"))
        profile = await p.fetch_profile(token_data)
    except Exception as e:
        logger.error("Failed to exchange code or fetch profile from provider", provider=provider, error=str(e), exc_info=True)
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="Failed to communicate with OAuth provider.")

    # Upsert local user (username/email best effort)
    username = f"{provider}_{profile.get('id')}"
    email = profile.get("email") or f"{username}@oauth.local"

    result = await db.execute(select(User).where(User.username == username))
    user = result.scalar_one_or_none()
    if not user:
        try:
            from app.core.security import get_password_hash
            from app.db.models import User as DBUser
            logger.info("Creating new user from OAuth profile", username=username, email=email)
            user = DBUser(
                username=username,
                email=email,
                hashed_password=get_password_hash(secrets.token_urlsafe(16)),
                is_active=True,
                is_admin=False,
            )
            db.add(user)
            await db.commit()
            await db.refresh(user)
        except IntegrityError:
            await db.rollback()
            logger.warning("Race condition during user creation, user likely already exists.", username=username)
            result = await db.execute(select(User).where(User.username == username))
            user = result.scalar_one_or_none()
            if not user:
                logger.critical("Failed to create or find user after integrity error.", username=username)
                raise HTTPException(status_code=500, detail="Could not create or find user account.")
        except Exception as e:
            logger.error("An unexpected error occurred during user creation.", username=username, error=str(e), exc_info=True)
            raise HTTPException(status_code=500, detail="An internal error occurred during user creation.")


    # Issue JWT and Refresh Token
    access_token = create_access_token(data={"sub": str(user.id), "user_id": str(user.id)})
    
    user_agent = request.headers.get("user-agent")
    ip_address = request.client.host if request.client else None
    refresh_token, db_refresh_token = create_refresh_token_object(
        user_id=user.id, user_agent=user_agent, ip_address=ip_address
    )
    db.add(db_refresh_token)
    await db.commit()

    # 生成一次性 ticket，缓存 token 对以供页面兑换
    ticket = secrets.token_urlsafe(24)
    try:
        await cache.set(
            f"ticket:{ticket}",
            {"access_token": access_token, "refresh_token": refresh_token},
            expire=120  # 两分钟有效期
        )
    except Exception as e:
        logger.error("Failed to store OAuth ticket in cache", ticket=ticket, error=str(e), exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to finalize OAuth login.")

    # Cleanup state
    try:
        await cache.delete(f"state:{state}")
    except Exception as e:
        logger.warning("Failed to delete OAuth state from cache", state=state, error=str(e))

    # Redirect to callback page with ticket only
    redirect_url = f"/static/callback.html?ticket={ticket}"
    return RedirectResponse(url=redirect_url)


@router.post("/auth/oauth/ticket/exchange", response_model=OAuthExchangeResponse)
async def oauth_exchange_ticket(
    body: TicketExchangeRequest,
):
    """Exchange a one-time ticket for OAuth tokens."""
    cache = get_cache(prefix="oauth")
    ticket_key = f"ticket:{body.ticket}"
    
    token_data = await cache.get(ticket_key)
    if not token_data:
        logger.warning("Invalid or expired ticket presented for exchange", ticket=body.ticket)
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Invalid or expired ticket.")

    # Delete the ticket to make it single-use
    try:
        await cache.delete(ticket_key)
    except Exception as e:
        # Log the error but proceed, as the user has the tokens.
        logger.warning("Failed to delete used OAuth ticket from cache", ticket=body.ticket, error=str(e))

    return {
        "access_token": token_data.get("access_token"),
        "refresh_token": token_data.get("refresh_token"),
    }


