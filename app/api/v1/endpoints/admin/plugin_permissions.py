# -*- coding: utf-8 -*-
"""
API endpoints for managing Plugin Permissions (lock files).
"""
from fastapi import APIRouter, Depends, HTTPException, Body
from typing import Dict, Any, Optional

from app.api.auth_deps import require_admin
from app.db.models import User
from app.services.plugin_permission_service import get_plugin_permission_service, PluginPermissionService
from app.utils.responses import APIResponse
from app.schemas.base import APIResponseSchema

router = APIRouter()

ServiceDep = Depends(get_plugin_permission_service)
AdminUserDep = Depends(require_admin)

@router.get("/{plugin_name}", response_model=APIResponseSchema[Dict[str, Any]])
async def get_plugin_permissions(
    plugin_name: str,
    service: PluginPermissionService = ServiceDep,
    current_user: User = AdminUserDep
):
    """Gets the contents of the permission lock file for a specific plugin.

    This file represents the actual permissions a plugin is granted at runtime.

    Args:
        plugin_name: The name of the plugin.
        service: The PluginPermissionService instance.
        current_user: The authenticated admin user.

    Raises:
        HTTPException: If the plugin or its lock file is not found.

    Returns:
        An APIResponse containing the plugin's permissions.
    """
    try:
        permissions = service.get_permissions(plugin_name)
        return APIResponse.success(data=permissions)
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"An unexpected error occurred: {e}")

@router.post("/{plugin_name}", response_model=APIResponseSchema[Dict[str, Any]])
async def add_permission_to_plugin(
    plugin_name: str,
    permission: Dict[str, Any] = Body(..., example={
        "type": "file.read",
        "resource": "data.csv",
        "description": "Read data file"
    }),
    service: PluginPermissionService = ServiceDep,
    current_user: User = AdminUserDep
):
    """Adds a new permission to a specific plugin's lock file.

    Warning: This is a powerful operation that directly modifies a plugin's
    security sandbox.

    Args:
        plugin_name: The name of the plugin to modify.
        permission: The permission object to add.
        service: The PluginPermissionService instance.
        current_user: The authenticated admin user.

    Raises:
        HTTPException: If the plugin is not found or the permission is invalid.

    Returns:
        An APIResponse containing the updated list of permissions.
    """
    try:
        new_permissions = service.add_permission(plugin_name, permission)
        return APIResponse.success(data=new_permissions, message="Permission added successfully.")
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"An unexpected error occurred: {e}")

@router.delete("/{plugin_name}", response_model=APIResponseSchema[Dict[str, Any]])
async def remove_permission_from_plugin(
    plugin_name: str,
    permission_type: str,
    resource: Optional[str] = None,
    service: PluginPermissionService = ServiceDep,
    current_user: User = AdminUserDep
):
    """Removes a permission from a specific plugin's lock file.

    Args:
        plugin_name: The name of the plugin to modify.
        permission_type: The 'type' of the permission to remove (e.g., "file.read").
        resource: The optional 'resource' to match for removal. If None, removes
                  the first permission matching the type.
        service: The PluginPermissionService instance.
        current_user: The authenticated admin user.

    Raises:
        HTTPException: If the plugin or permission is not found.

    Returns:
        An APIResponse containing the updated list of permissions.
    """
    try:
        new_permissions = service.remove_permission(plugin_name, permission_type, resource)
        return APIResponse.success(data=new_permissions, message="Permission removed successfully.")
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"An unexpected error occurred: {e}")