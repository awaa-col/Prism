"""
Security utilities for authentication and authorization.
"""

from datetime import datetime, timedelta
from typing import Optional, Dict, Any, List

from jose import JWTError, jwt
from passlib.context import CryptContext

from app.core.config import get_settings
import hashlib
import hmac
import secrets
import uuid
from redis.exceptions import RedisError
from app.core.cache import get_cache
from app.db.models import RefreshToken
from app.core.structured_logging import get_logger


logger = get_logger(__name__)


# Password hashing context
# Manually specify the bcrypt backend to avoid auto-detection issues
pwd_context = CryptContext(schemes=["bcrypt"], bcrypt__rounds=12)

# Get settings
settings = get_settings()


def get_refresh_token_hash(token: str) -> str:
    """Hashes a refresh token using SHA-256."""
    return hashlib.sha256(token.encode()).hexdigest()


def create_refresh_token_object(
    user_id: str, user_agent: str | None, ip_address: str | None
) -> tuple[str, RefreshToken]:
    """
    Creates a new refresh token and its corresponding database object.
    
    Returns:
        A tuple containing the raw token string and the RefreshToken database model instance.
        The instance is NOT yet committed to the database.
    """
    token = secrets.token_urlsafe(32)
    token_hash = get_refresh_token_hash(token)
    
    expires_delta = timedelta(days=settings.security.refresh_token_expire_days)
    expires_at = datetime.utcnow() + expires_delta

    db_refresh_token = RefreshToken(
        user_id=user_id,
        token_hash=token_hash,
        expires_at=expires_at,
        user_agent=user_agent,
        ip_address=ip_address,
    )
    return token, db_refresh_token


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify a password against its hash"""
    return pwd_context.verify(plain_password, hashed_password)


def get_password_hash(password: str) -> str:
    """Generate password hash"""
    return pwd_context.hash(password)


def create_access_token(data: Dict[str, Any], expires_delta: Optional[timedelta] = None) -> str:
    """Create a JWT access token"""
    to_encode = data.copy()
    # Ensure JTI exists for revocation
    if "jti" not in to_encode:
        to_encode["jti"] = str(uuid.uuid4())
    
    if expires_delta:
        expire = datetime.utcnow() + expires_delta
    else:
        expire = datetime.utcnow() + timedelta(minutes=settings.security.access_token_expire_minutes)
    
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(
        to_encode, 
        settings.security.secret_key, 
        algorithm=settings.security.algorithm
    )
    return encoded_jwt


def decode_access_token(token: str) -> Optional[Dict[str, Any]]:
    """Decode and verify a JWT access token"""
    try:
        payload = jwt.decode(
            token, 
            settings.security.secret_key, 
            algorithms=[settings.security.algorithm]
        )
        return payload
    except JWTError:
        return None


def create_api_key_token(api_key_id: str, user_id: str, permissions: List[str]) -> str:
    """Create a token for API key authentication"""
    data = {
        "sub": api_key_id,
        "user_id": user_id,
        "permissions": permissions,
        "type": "api_key"
    }
    return create_access_token(data)


def verify_api_key_token(token: str) -> Optional[Dict[str, Any]]:
    """Verify an API key token"""
    payload = decode_access_token(token)
    if payload and payload.get("type") == "api_key":
        return payload
    return None 


def hash_api_key(plain_key: str) -> str:
    """One-way hash for API keys using SHA-256 (suitable for high-entropy keys)."""
    return hashlib.sha256(plain_key.encode("utf-8")).hexdigest()


def verify_api_key_plain(plain_key: str, stored_hash_hex: str) -> bool:
    """Verify API key by hashing and constant-time comparing."""
    computed = hash_api_key(plain_key)
    try:
        return hmac.compare_digest(computed, stored_hash_hex)
    except Exception:
        return False 


async def revoke_token_jti(jti: str, ttl_seconds: int) -> None:
    """Add token JTI to blacklist with TTL."""
    cache = get_cache(prefix="token_blacklist")
    try:
        await cache.set(jti, True, expire=max(1, ttl_seconds))
    except RedisError as e:
        # Best effort; do not raise but log the error.
        logger.warning("Failed to add token JTI to Redis blacklist", jti=jti, error=str(e))
        pass


async def is_token_jti_revoked(jti: Optional[str]) -> bool:
    """Check if JTI is blacklisted."""
    if not jti:
        return False
    cache = get_cache(prefix="token_blacklist")
    try:
        return bool(await cache.get(jti))
    except RedisError as e:
        # If cache is down, fail open (treat as not revoked) but log the error.
        logger.warning("Failed to check token JTI in Redis blacklist", jti=jti, error=str(e))
        return False