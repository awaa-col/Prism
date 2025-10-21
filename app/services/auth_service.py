"""
Service layer for handling authentication logic.
"""
from datetime import timedelta, datetime
from typing import Dict, Any, Optional

from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db.models import User, RefreshToken
from app.core.security import (
    verify_password,
    create_access_token,
    revoke_token_jti,
    decode_access_token,
    create_refresh_token_object,
    get_refresh_token_hash,
)
from app.core.config import get_settings
from app.utils.responses import APIException
from app.schemas.token import TokenResponse

settings = get_settings()

class AuthService:
    async def login_user(
        self,
        db: AsyncSession,
        username: str,
        password: str,
        user_agent: Optional[str],
        ip_address: Optional[str]
    ) -> Dict[str, Any]:
        
        result = await db.execute(select(User).where(User.username == username))
        user = result.scalar_one_or_none()

        if not user or not verify_password(password, user.hashed_password):
            raise APIException(
                message="Invalid username or password",
                code="auth_invalid_credentials",
                status_code=401
            )

        if not user.is_active:
            raise APIException(
                message="User account is inactive",
                code="auth_user_inactive",
                status_code=401
            )

        access_token_expires = timedelta(minutes=settings.security.access_token_expire_minutes)
        access_token = create_access_token(
            data={"sub": str(user.id), "user_id": str(user.id)},
            expires_delta=access_token_expires
        )

        refresh_token, db_refresh_token = create_refresh_token_object(
            user_id=str(user.id), user_agent=user_agent, ip_address=ip_address
        )
        db.add(db_refresh_token)
        await db.commit()

        return {
            "access_token": access_token,
            "token_type": "bearer",
            "expires_in": int(access_token_expires.total_seconds()),
            "refresh_token": refresh_token,
        }

    async def refresh_token(
        self,
        db: AsyncSession,
        refresh_token: str,
        user_agent: Optional[str],
        ip_address: Optional[str]
    ) -> Dict[str, Any]:
        token_hash = get_refresh_token_hash(refresh_token)

        result = await db.execute(
            select(RefreshToken)
            .where(RefreshToken.token_hash == token_hash)
            .options(selectinload(RefreshToken.user))
        )
        db_refresh_token = result.scalar_one_or_none()

        if not db_refresh_token or db_refresh_token.expires_at < datetime.utcnow():
            raise APIException(
                message="Invalid or expired refresh token",
                code="invalid_refresh_token",
                status_code=401
            )

        user = db_refresh_token.user
        if not user.is_active:
            raise APIException(
                message="User is inactive",
                code="auth_user_inactive",
                status_code=401
            )

        access_token_expires = timedelta(minutes=settings.security.access_token_expire_minutes)
        access_token = create_access_token(
            data={"sub": str(user.id), "user_id": str(user.id)},
            expires_delta=access_token_expires
        )
        
        await db.delete(db_refresh_token)
        
        new_refresh_token, new_db_refresh_token = create_refresh_token_object(
            user_id=str(user.id), user_agent=user_agent, ip_address=ip_address
        )
        db.add(new_db_refresh_token)
        await db.commit()
        
        return {
            "access_token": access_token,
            "token_type": "bearer",
            "expires_in": int(access_token_expires.total_seconds()),
            "refresh_token": new_refresh_token,
        }

    async def logout_user(
        self,
        db: AsyncSession,
        user: User,
        access_token: Optional[str],
        refresh_token: str
    ):
        if access_token:
            payload = decode_access_token(access_token) or {}
            jti = payload.get("jti")
            exp = payload.get("exp")
            if jti and exp:
                now_ts = int(datetime.utcnow().timestamp())
                ttl = max(1, int(exp) - now_ts)
                await revoke_token_jti(jti, ttl)

        token_hash = get_refresh_token_hash(refresh_token)
        
        await db.execute(
            delete(RefreshToken).where(
                RefreshToken.token_hash == token_hash,
                RefreshToken.user_id == user.id
            )
        )
        await db.commit()

def get_auth_service() -> AuthService:
    return AuthService()