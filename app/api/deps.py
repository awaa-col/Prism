"""
FastAPI dependencies for authentication, database sessions, etc.
"""

from typing import Optional, Annotated
from datetime import datetime

from fastapi import Depends, HTTPException, status, Header, Request
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update

from app.db.session import get_db
from app.db.models import User, APIKey
from app.core.security import verify_api_key_token, verify_password, verify_api_key_plain, hash_api_key
from app.core.cache import get_cache, get_rate_limiter
from app.core.structured_logging import get_logger
from app.core.security import decode_access_token
from app.core.security import is_token_jti_revoked

logger = get_logger("api.deps")

# Security scheme
security = HTTPBearer()


async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security),
    db: AsyncSession = Depends(get_db)
) -> User:
    """
    Get current user from JWT token.
    Used for admin endpoints.
    """
    
    token = credentials.credentials
    
    # Decode token
    payload = decode_access_token(token)
    if not payload:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )
    # JTI 黑名单校验
    jti = payload.get("jti")
    if await is_token_jti_revoked(jti):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token revoked",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    # Get user from database  
    user_id = payload.get("user_id") or payload.get("sub")
    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token payload"
        )
    
    result = await db.execute(
        select(User).where(User.id == user_id)
    )
    user = result.scalar_one_or_none()
    
    if not user or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found or inactive"
        )
    
    return user


async def get_current_admin_user(
    current_user: User = Depends(get_current_user)
) -> User:
    """
    Get current admin user.
    Used for admin-only endpoints.
    """
    if not current_user.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not enough permissions"
        )
    return current_user


async def get_api_key_or_user(
    authorization: Optional[str] = Header(None),
    db: AsyncSession = Depends(get_db)
) -> APIKey:
    """
    Get API key from Authorization header or create one from JWT token.
    Used for API endpoints that support both API keys and JWT tokens.
    """
    from app.core.security import decode_access_token
    
    if not authorization:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authorization header missing"
        )
    
    # Extract token from "Bearer <token>"
    parts = authorization.split()
    if len(parts) != 2 or parts[0].lower() != "bearer":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authorization header format"
        )
    
    token = parts[1]
    
    # First try as JWT token
    payload = decode_access_token(token)
    if payload:
        # This is a JWT token from user login
        user_id = payload.get("user_id") or payload.get("sub")
        if user_id:
            # Get user's primary API key or create a virtual one
            result = await db.execute(
                select(APIKey).where(
                    APIKey.user_id == user_id,
                    APIKey.is_active == True
                ).order_by(APIKey.created_at.desc())
            )
            api_key = result.scalar_one_or_none()
            
            if api_key:
                return api_key
            
            # If no API key exists, create a virtual one for this session
            # This allows JWT authenticated users to access model APIs
            from app.db.models import User
            result = await db.execute(
                select(User).where(User.id == user_id)
            )
            user = result.scalar_one_or_none()
            if user and user.is_active:
                # Create a virtual API key object
                virtual_key = APIKey()
                virtual_key.id = f"jwt-{user_id}"
                virtual_key.user_id = user_id
                virtual_key.is_active = True
                virtual_key.rate_limit = None  # Use default
                virtual_key.rate_limit_period = 60
                virtual_key.expires_at = None
                virtual_key.allowed_models = []  # Allow all models
                return virtual_key
    
    # Try as regular API key
    cache = get_cache(prefix="api_key")
    # 使用哈希作为缓存键，避免在缓存后端暴露明文
    token_hash = hash_api_key(token)
    cached_key = await cache.get(token_hash)
    
    if cached_key:
        # Deserialize from cache
        api_key_id = cached_key.get("id")
        result = await db.execute(
            select(APIKey).where(APIKey.id == api_key_id)
        )
        api_key = result.scalar_one_or_none()
    else:
        # 直接按哈希等值查询（高效且不暴露明文）
        result = await db.execute(select(APIKey).where(APIKey.key == token_hash))
        api_key = result.scalar_one_or_none()
        
        if api_key:
            # Cache for future requests
            await cache.set(
                token_hash,
                {
                    "id": str(api_key.id),
                    "user_id": str(api_key.user_id),
                    "rate_limit": api_key.rate_limit,
                    "rate_limit_period": api_key.rate_limit_period
                },
                expire=300  # 5 minutes
            )
    
    if not api_key or not api_key.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or inactive API key"
        )
    
    # Check expiration (skip for virtual keys)
    if hasattr(api_key, 'expires_at') and api_key.expires_at and api_key.expires_at < datetime.utcnow():
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="API key has expired"
        )
    
    # Update last used timestamp (skip for virtual keys)
    if not str(api_key.id).startswith('jwt-'):
        await db.execute(
            update(APIKey)
            .where(APIKey.id == api_key.id)
            .values(last_used_at=datetime.utcnow())
        )
    
    return api_key


async def get_api_key(
    authorization: Optional[str] = Header(None),
    db: AsyncSession = Depends(get_db)
) -> APIKey:
    """
    Get API key from Authorization header (API key only).
    Used for legacy API endpoints that require API keys.
    """
    return await get_api_key_or_user(authorization, db)


async def check_rate_limit(
    request: Request,
    api_key: APIKey = Depends(get_api_key)
) -> None:
    """
    Check rate limit for API key.
    """
    # Use custom rate limit if set, otherwise use defaults
    from app.core.config import get_settings
    settings = get_settings()
    
    rate_limit = api_key.rate_limit or settings.rate_limiting.default_limit
    rate_period = api_key.rate_limit_period or settings.rate_limiting.default_period
    
    # Check rate limit
    limiter = get_rate_limiter()
    is_allowed, remaining = await limiter.is_allowed(
        key=str(api_key.id),
        limit=rate_limit,
        period=rate_period
    )
    
    # Add rate limit headers
    request.state.rate_limit_remaining = remaining
    request.state.rate_limit_limit = rate_limit
    request.state.rate_limit_reset = int(datetime.utcnow().timestamp()) + rate_period
    
    if not is_allowed:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Rate limit exceeded",
            headers={
                "X-RateLimit-Limit": str(rate_limit),
                "X-RateLimit-Remaining": "0",
                "X-RateLimit-Reset": str(request.state.rate_limit_reset)
            }
        )


async def get_request_id(request: Request) -> str:
    """Get or generate request ID"""
    request_id = request.headers.get("X-Request-ID")
    if not request_id:
        import uuid
        request_id = str(uuid.uuid4())
    return request_id


# Type aliases for cleaner function signatures
CurrentUser = Annotated[User, Depends(get_current_user)]
CurrentAdminUser = Annotated[User, Depends(get_current_admin_user)]
CurrentAPIKey = Annotated[APIKey, Depends(get_api_key_or_user)]
DBSession = Annotated[AsyncSession, Depends(get_db)]
RequestID = Annotated[str, Depends(get_request_id)] 