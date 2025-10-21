"""
User and authentication related database models.
"""
from datetime import datetime, timezone
from typing import List, Optional, TYPE_CHECKING
import uuid
from uuid import UUID

from sqlalchemy import (
    Column, String, Boolean, DateTime, ForeignKey
)
from sqlalchemy.orm import relationship, Mapped, mapped_column

from app.db.models.base import Base, GUID

if TYPE_CHECKING:
    from .api_key import APIKey
    from .plugin import Credential
    from .rbac import Role

class User(Base):
    """User model"""
    __tablename__ = "users"
    
    id: Mapped[UUID] = mapped_column(GUID(), primary_key=True, default=uuid.uuid4)
    username: Mapped[str] = mapped_column(String(50), unique=True, nullable=False, index=True)
    email: Mapped[str] = mapped_column(String(100), unique=True, nullable=False, index=True)
    hashed_password: Mapped[str] = mapped_column(String(100), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))
    
    api_keys: Mapped[List["APIKey"]] = relationship("APIKey", back_populates="user", cascade="all, delete-orphan")
    credentials: Mapped[List["Credential"]] = relationship("Credential", back_populates="user", cascade="all, delete-orphan")
    refresh_tokens: Mapped[List["RefreshToken"]] = relationship("RefreshToken", back_populates="user", cascade="all, delete-orphan")
    
    roles: Mapped[List["Role"]] = relationship(
        "Role",
        secondary="user_roles",
        back_populates="users",
        lazy="selectin"
    )

class RefreshToken(Base):
    """Refresh Token for persistent sessions"""
    __tablename__ = "refresh_tokens"

    id: Mapped[UUID] = mapped_column(GUID(), primary_key=True, default=uuid.uuid4)
    token_hash: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    user_id: Mapped[UUID] = mapped_column(GUID(), ForeignKey("users.id"), nullable=False)
    user_agent: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    ip_address: Mapped[Optional[str]] = mapped_column(String(45), nullable=True)
    
    created_at: Mapped[datetime] = mapped_column(default=lambda: datetime.now(timezone.utc))
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    
    user = relationship("User", back_populates="refresh_tokens")

class AuthorizationCode(Base):
    __tablename__ = "oauth2_authorization_codes"
    id: Mapped[UUID] = mapped_column(GUID(), primary_key=True, default=uuid.uuid4)

class OAuth2Token(Base):
    __tablename__ = 'oauth2_tokens'
    id: Mapped[UUID] = mapped_column(GUID(), primary_key=True, default=uuid.uuid4)