from typing import Generator, Optional

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from jose import jwt
from pydantic import ValidationError
from sqlalchemy.orm import Session, selectinload
from sqlalchemy import select

from app.core import security
from app.core.config import settings
from app.db.session import SessionLocal, get_db
from app.db.models import User
from app.api.auth_deps import require_user


def require_permission(required_permission: str):
    """
    FastAPI dependency that checks if a user has a specific permission.
    """
    async def _require_permission(current_user: User = Depends(require_user)) -> User:
        # Superusers bypass all permission checks
        if current_user.is_admin:
            return current_user
        
        # Check if the user has the required permission
        has_permission = any(p.name == required_permission for p in current_user.permissions)
        
        if not has_permission:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You do not have sufficient permissions to perform this action.",
            )
        
        return current_user
    
    return _require_permission



