"""
RBAC (Role-Based Access Control) related database models.
"""
from typing import List, Optional, TYPE_CHECKING
import uuid
from uuid import UUID

from sqlalchemy import (
    Column, String, Text, ForeignKey, Table
)
from sqlalchemy.orm import relationship, Mapped, mapped_column

from app.db.models.base import Base, GUID

if TYPE_CHECKING:
    from .user import User

user_roles = Table(
    'user_roles',
    Base.metadata,
    Column('user_id', GUID, ForeignKey('users.id'), primary_key=True),
    Column('role_id', GUID, ForeignKey('roles.id'), primary_key=True)
)

role_permissions = Table(
    'role_permissions',
    Base.metadata,
    Column('role_id', GUID, ForeignKey('roles.id'), primary_key=True),
    Column('permission_id', GUID, ForeignKey('permissions.id'), primary_key=True)
)

class Role(Base):
    __tablename__ = 'roles'
    
    id: Mapped[UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(100), unique=True, index=True, nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text)
    
    users: Mapped[List["User"]] = relationship(
        "User",
        secondary=user_roles,
        back_populates="roles"
    )
    
    permissions: Mapped[List["Permission"]] = relationship(
        "Permission",
        secondary=role_permissions,
        back_populates="roles",
        lazy="selectin"
    )

class Permission(Base):
    __tablename__ = 'permissions'
    
    id: Mapped[UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text)
    
    roles: Mapped[List["Role"]] = relationship(
        "Role",
        secondary=role_permissions,
        back_populates="permissions"
    )