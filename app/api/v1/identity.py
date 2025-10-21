"""
Minimal Identity API, refactored to use a service layer.
"""
from typing import Dict, Any
from fastapi import APIRouter, Depends, status, Request, Header, Response
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db
from app.api.auth_deps import require_user
from app.db.models import User
from app.core.config import get_settings
from app.core.structured_logging import get_logger
from app.schemas.token import TokenResponse, RefreshTokenRequest, LogoutRequest
from app.services.auth_service import AuthService, get_auth_service

router = APIRouter()
logger = get_logger("api.identity")
settings = get_settings()


class UserLogin(BaseModel):
    username: str
    password: str


# region --- Authentication Endpoints ---

@router.post("/login", response_model=TokenResponse, tags=["Authentication"])
async def login_user(
    response: Response,
    login_data: UserLogin,
    request: Request,
    db: AsyncSession = Depends(get_db),
    auth_service: AuthService = Depends(get_auth_service)
) -> Dict[str, Any]:
    """Login user and return access and refresh tokens."""
    logger.info("User login attempt", username=login_data.username)
    
    user_agent = request.headers.get("user-agent")
    ip_address = request.client.host if request.client else None

    token_data = await auth_service.login_user(
        db=db,
        username=login_data.username,
        password=login_data.password,
        user_agent=user_agent,
        ip_address=ip_address
    )
    
    logger.info("User logged in successfully", username=login_data.username)

    response.set_cookie(
        key="session_id",
        value=token_data["access_token"],
        httponly=True,
        secure=not settings.debug,
        samesite="lax",
        max_age=token_data["expires_in"]
    )

    return token_data


class UserProfile(BaseModel):
    id: str
    username: str
    email: str | None = None
    is_admin: bool
    is_active: bool


@router.get("/me", response_model=UserProfile, tags=["Authentication"])
async def get_me(current_user: User = Depends(require_user)) -> UserProfile:
    """Return current authenticated user's minimal profile."""
    return UserProfile(
        id=str(current_user.id),
        username=current_user.username or current_user.email or str(current_user.id),
        email=current_user.email,
        is_admin=current_user.is_admin,
        is_active=current_user.is_active,
    )


@router.post("/refresh", response_model=TokenResponse, tags=["Authentication"])
async def refresh_access_token(
    token_data: RefreshTokenRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    auth_service: AuthService = Depends(get_auth_service)
) -> Dict[str, Any]:
    """Refresh the access token using a valid refresh token."""
    user_agent = request.headers.get("user-agent")
    ip_address = request.client.host if request.client else None

    new_token_data = await auth_service.refresh_token(
        db=db,
        refresh_token=token_data.refresh_token,
        user_agent=user_agent,
        ip_address=ip_address
    )
    
    logger.info("Access token refreshed and refresh token rotated")
    
    return new_token_data


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT, tags=["Authentication"])
async def logout_user(
    logout_data: LogoutRequest,
    authorization: str | None = Header(None),
    current_user: User = Depends(require_user),
    db: AsyncSession = Depends(get_db),
    auth_service: AuthService = Depends(get_auth_service)
):
    """
    Logout user. Adds the access token's JTI to a blacklist and deletes the refresh token.
    """
    token = None
    if authorization and authorization.lower().startswith("bearer "):
        token = authorization.split(" ")[1]

    await auth_service.logout_user(
        db=db,
        user=current_user,
        access_token=token,
        refresh_token=logout_data.refresh_token
    )
    
    logger.info("User logged out successfully", user_id=str(current_user.id))
    
    return

# endregion
