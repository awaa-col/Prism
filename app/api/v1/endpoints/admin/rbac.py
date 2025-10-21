# -*- coding: utf-8 -*-
"""
API endpoints for managing Role-Based Access Control (RBAC).
"""
from typing import List
from uuid import UUID

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth_deps import require_admin
from app.db.models import User
from app.db.session import get_db
from app.schemas.rbac import (
    PermissionCreate, PermissionSchema,
    RoleCreate, RoleSchema,
    RolePermissionRequest, UserRoleRequest, UserRolesResponse
)
from app.services.rbac_service import get_rbac_service, RbacService
from app.utils.responses import APIResponse
from app.schemas.base import APIResponseSchema
from app.core.structured_logging import get_logger

router = APIRouter()
logger = get_logger("api.admin.rbac")

# Dependency for this router
RBACServiceDep = Depends(get_rbac_service)
DBSessionDep = Depends(get_db)
AdminUserDep = Depends(require_admin)

# --- Permissions ---

@router.post("/permissions", response_model=APIResponseSchema[PermissionSchema], status_code=status.HTTP_201_CREATED)
async def create_permission(
    permission_data: PermissionCreate,
    db: AsyncSession = DBSessionDep,
    service: RbacService = RBACServiceDep,
    current_user: User = AdminUserDep,
):
    """Creates a new permission definition in the system.

    Args:
        permission_data: The data for the new permission.
        db: The database session.
        service: The RBAC service instance.
        current_user: The authenticated admin user.

    Returns:
        An APIResponse containing the newly created permission.
    """
    permission = await service.create_permission(db, permission_data)
    logger.info("Permission created", name=permission.name, by=current_user.username)
    return APIResponse.success(data=permission, message="Permission created successfully.")

@router.get("/permissions", response_model=APIResponseSchema[List[PermissionSchema]])
async def list_permissions(
    db: AsyncSession = DBSessionDep,
    service: RbacService = RBACServiceDep,
    current_user: User = AdminUserDep,
):
    """Lists all available permission definitions in the system.

    Args:
        db: The database session.
        service: The RBAC service instance.
        current_user: The authenticated admin user.

    Returns:
        An APIResponse containing a list of all permissions.
    """
    permissions = await service.list_permissions(db)
    return APIResponse.success(data=permissions)

# --- Roles ---

@router.post("/roles", response_model=APIResponseSchema[RoleSchema], status_code=status.HTTP_201_CREATED)
async def create_role(
    role_data: RoleCreate,
    db: AsyncSession = DBSessionDep,
    service: RbacService = RBACServiceDep,
    current_user: User = AdminUserDep,
):
    """Creates a new role in the system.

    Args:
        role_data: The data for the new role.
        db: The database session.
        service: The RBAC service instance.
        current_user: The authenticated admin user.

    Returns:
        An APIResponse containing the newly created role.
    """
    role = await service.create_role(db, role_data)
    logger.info("Role created", name=role.name, by=current_user.username)
    return APIResponse.success(data=role, message="Role created successfully.")

@router.get("/roles", response_model=APIResponseSchema[List[RoleSchema]])
async def list_roles(
    db: AsyncSession = DBSessionDep,
    service: RbacService = RBACServiceDep,
    current_user: User = AdminUserDep,
):
    """Lists all available roles and their assigned permissions.

    Args:
        db: The database session.
        service: The RBAC service instance.
        current_user: The authenticated admin user.

    Returns:
        An APIResponse containing a list of all roles.
    """
    roles = await service.list_roles(db)
    return APIResponse.success(data=roles)

@router.post("/roles/{role_id}/permissions", response_model=APIResponseSchema[RoleSchema])
async def add_permission_to_role(
    role_id: UUID,
    request: RolePermissionRequest,
    db: AsyncSession = DBSessionDep,
    service: RbacService = RBACServiceDep,
    current_user: User = AdminUserDep,
):
    """Adds a permission to a specific role.

    Args:
        role_id: The ID of the role to modify.
        request: The request containing the permission name to add.
        db: The database session.
        service: The RBAC service instance.
        current_user: The authenticated admin user.

    Returns:
        An APIResponse containing the updated role.
    """
    role = await service.add_permission_to_role(db, role_id, request.permission_name)
    logger.info("Permission added to role", role=role.name, permission=request.permission_name, by=current_user.username)
    return APIResponse.success(data=role, message="Permission added to role.")

@router.delete("/roles/{role_id}/permissions", response_model=APIResponseSchema[RoleSchema])
async def remove_permission_from_role(
    role_id: UUID,
    request: RolePermissionRequest,
    db: AsyncSession = DBSessionDep,
    service: RbacService = RBACServiceDep,
    current_user: User = AdminUserDep,
):
    """Removes a permission from a specific role.

    Args:
        role_id: The ID of the role to modify.
        request: The request containing the permission name to remove.
        db: The database session.
        service: The RBAC service instance.
        current_user: The authenticated admin user.

    Returns:
        An APIResponse containing the updated role.
    """
    role = await service.remove_permission_from_role(db, role_id, request.permission_name)
    logger.info("Permission removed from role", role=role.name, permission=request.permission_name, by=current_user.username)
    return APIResponse.success(data=role, message="Permission removed from role.")

# --- User-Role Assignments ---

@router.post("/users/{user_id}/roles", response_model=APIResponseSchema[UserRolesResponse])
async def assign_role_to_user(
    user_id: UUID,
    request: UserRoleRequest,
    db: AsyncSession = DBSessionDep,
    service: RbacService = RBACServiceDep,
    current_user: User = AdminUserDep,
):
    """Assigns a role to a specific user.

    Args:
        user_id: The ID of the user to modify.
        request: The request containing the role name to assign.
        db: The database session.
        service: The RBAC service instance.
        current_user: The authenticated admin user.

    Returns:
        An APIResponse containing the user's updated roles.
    """
    user = await service.assign_role_to_user(db, user_id, request.role_name)
    # Re-fetch the full user object for the response
    user_response = await service.get_user_with_roles(db, user_id)
    logger.info("Role assigned to user", user=user.username, role=request.role_name, by=current_user.username)
    return APIResponse.success(data=UserRolesResponse.model_validate(user_response), message="Role assigned successfully.")

@router.delete("/users/{user_id}/roles", response_model=APIResponseSchema[UserRolesResponse])
async def revoke_role_from_user(
    user_id: UUID,
    request: UserRoleRequest,
    db: AsyncSession = DBSessionDep,
    service: RbacService = RBACServiceDep,
    current_user: User = AdminUserDep,
):
    """Revokes a role from a specific user.

    Args:
        user_id: The ID of the user to modify.
        request: The request containing the role name to revoke.
        db: The database session.
        service: The RBAC service instance.
        current_user: The authenticated admin user.

    Returns:
        An APIResponse containing the user's updated roles.
    """
    user = await service.revoke_role_from_user(db, user_id, request.role_name)
    # Re-fetch the full user object for the response
    user_response = await service.get_user_with_roles(db, user_id)
    logger.info("Role revoked from user", user=user.username, role=request.role_name, by=current_user.username)
    return APIResponse.success(data=UserRolesResponse.model_validate(user_response), message="Role revoked successfully.")

@router.get("/users/{user_id}/roles", response_model=APIResponseSchema[UserRolesResponse])
async def get_user_roles(
    user_id: UUID,
    db: AsyncSession = DBSessionDep,
    service: RbacService = RBACServiceDep,
    current_user: User = AdminUserDep,
):
    """Gets a specific user's assigned roles and their effective permissions.

    Args:
        user_id: The ID of the user to query.
        db: The database session.
        service: The RBAC service instance.
        current_user: The authenticated admin user.

    Returns:
        An APIResponse containing the user's roles and permissions.
    """
    user = await service.get_user_with_roles(db, user_id)
    return APIResponse.success(data=UserRolesResponse.model_validate(user))