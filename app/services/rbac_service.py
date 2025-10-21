# -*- coding: utf-8 -*-
"""
Service layer for handling Role-Based Access Control (RBAC).
This service encapsulates all business logic for managing Users, Roles, and Permissions.
"""
from typing import List
from uuid import UUID
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db.models import Permission, User, Role
from app.schemas.rbac import PermissionCreate, RoleCreate
from app.utils.responses import APIException
from app.core.structured_logging import get_logger

logger = get_logger("services.rbac")


class RbacService:
    """Manages Users, Roles, and Permissions."""

    async def create_permission(self, db: AsyncSession, permission_data: PermissionCreate) -> Permission:
        """Creates a new permission definition."""
        existing = await db.scalar(select(Permission).where(Permission.name == permission_data.name))
        if existing:
            raise APIException(409, "Permission with this name already exists.")
        
        new_permission = Permission(**permission_data.model_dump())
        db.add(new_permission)
        await db.commit()
        await db.refresh(new_permission)
        return new_permission

    async def list_permissions(self, db: AsyncSession) -> List[Permission]:
        """Lists all available permission definitions."""
        result = await db.execute(select(Permission).order_by(Permission.name))
        return list(result.scalars().all())

    async def create_role(self, db: AsyncSession, role_data: RoleCreate) -> Role:
        """Creates a new role."""
        existing = await db.scalar(select(Role).where(Role.name == role_data.name))
        if existing:
            raise APIException(409, "Role with this name already exists.")

        new_role = Role(**role_data.model_dump())
        db.add(new_role)
        await db.commit()
        await db.refresh(new_role)
        return new_role

    async def list_roles(self, db: AsyncSession) -> List[Role]:
        """Lists all available roles and their assigned permissions."""
        result = await db.execute(select(Role).options(selectinload(Role.permissions)).order_by(Role.name))
        return list(result.scalars().all())

    async def add_permission_to_role(self, db: AsyncSession, role_id: UUID, permission_name: str) -> Role:
        """Adds a permission to a role."""
        role = await db.get(Role, role_id, options=[selectinload(Role.permissions)])
        if not role:
            raise APIException(404, "Role not found.")
        
        permission = await db.scalar(select(Permission).where(Permission.name == permission_name))
        if not permission:
            raise APIException(404, f"Permission '{permission_name}' not found.")
            
        if permission not in role.permissions:
            role.permissions.append(permission)
            await db.commit()
            await db.refresh(role)
        return role

    async def remove_permission_from_role(self, db: AsyncSession, role_id: UUID, permission_name: str) -> Role:
        """Removes a permission from a role."""
        role = await db.get(Role, role_id, options=[selectinload(Role.permissions)])
        if not role:
            raise APIException(404, "Role not found.")

        permission_to_remove = next((p for p in role.permissions if p.name == permission_name), None)
        if permission_to_remove:
            role.permissions.remove(permission_to_remove)
            await db.commit()
            await db.refresh(role)
        return role

    async def assign_role_to_user(self, db: AsyncSession, user_id: UUID, role_name: str) -> User:
        """Assigns a role to a user."""
        user = await db.get(User, user_id, options=[selectinload(User.roles)])
        if not user:
            raise APIException(404, "User not found.")
            
        role = await db.scalar(select(Role).where(Role.name == role_name))
        if not role:
            raise APIException(404, f"Role '{role_name}' not found.")
            
        if role not in user.roles:
            user.roles.append(role)
            await db.commit()
            await db.refresh(user)
        return user

    async def revoke_role_from_user(self, db: AsyncSession, user_id: UUID, role_name: str) -> User:
        """Revokes a role from a user."""
        user = await db.get(User, user_id, options=[selectinload(User.roles)])
        if not user:
            raise APIException(404, "User not found.")

        role_to_revoke = next((r for r in user.roles if r.name == role_name), None)
        if role_to_revoke:
            user.roles.remove(role_to_revoke)
            await db.commit()
            await db.refresh(user)
        return user

    async def get_user_with_roles(self, db: AsyncSession, user_id: UUID) -> User:
        """Gets a user and all their roles and permissions."""
        user = await db.get(User, user_id, options=[selectinload(User.roles).selectinload(Role.permissions)])
        if not user:
            raise APIException(404, "User not found.")
        return user

# Dependency provider
def get_rbac_service() -> "RbacService":
    return RbacService()