from typing import List, Dict, Any, Optional
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from app.api.auth_deps import require_admin
from app.db.models import User
from app.core.structured_logging import get_logger
from app.schemas.base import APIResponseSchema
from app.services.plugin_manager import get_plugin_manager, PluginManager
from app.plugins.loader import PluginNotFoundException
from app.services.plugin_installer import (
    get_installer_service,
    PluginInstallerService,
    InstallationError,
    PermissionRequest as PermissionRequestModel,
    DependencyConflict as DependencyConflictModel,
)

router = APIRouter()
logger = get_logger("api.admin.plugins")


# --- Models for Installation ---

class PermissionRequest(BaseModel):
    type: str
    resource: Optional[str]
    description: str
    risk_level: str

class PermissionDiff(BaseModel):
    added: List[PermissionRequest]
    removed: List[PermissionRequest]
    unchanged: List[PermissionRequest]

class DependencyConflict(BaseModel):
    package: str
    current_version: Optional[str]
    required_version: str
    conflict_type: str

class StartInstallationResponse(BaseModel):
    stage: str
    context_id: str
    is_update: bool
    permissions: List[PermissionRequest]
    permission_diff: PermissionDiff
    dependencies: List[str]
    conflicts: List[DependencyConflict]
    message: str

class InstallationStageResponse(BaseModel):
    stage: str
    context_id: str
    message: str

class FinishInstallationResponse(BaseModel):
    status: str
    plugin_name: str
    message: str


class SecureInstallAction(BaseModel):
    context_id: str = Field(..., description="The context ID returned by the 'start' stage.")

class ApprovePermissionsRequest(SecureInstallAction):
    approved_permissions: List[Dict[str, Any]] = Field(..., description="A list of permission objects that the admin has approved.")

class ResolveDependenciesRequest(SecureInstallAction):
    strategy: str = Field("auto", description="The strategy to use for resolving dependencies ('auto' or 'force').")

# --- Refactored Endpoints using Service Layer ---

@router.get("/", response_model=APIResponseSchema[List[Dict[str, Any]]])
async def list_plugins(
    current_user: User = Depends(require_admin),
    manager: PluginManager = Depends(get_plugin_manager)
) -> APIResponseSchema[List[Dict[str, Any]]]:
    """Lists all currently loaded and active plugins."""
    if not manager:
        raise HTTPException(status_code=503, detail="Plugin manager not initialized")
    
    plugin_list = await manager.list_plugins()
    logger.info("Plugin list requested", plugin_count=len(plugin_list), user_id=str(current_user.id))
    return APIResponseSchema(data=plugin_list, message="Plugins retrieved successfully")

@router.post("/{plugin_name}/reload", response_model=APIResponseSchema)
async def reload_plugin(
    plugin_name: str, 
    current_user: User = Depends(require_admin),
    manager: PluginManager = Depends(get_plugin_manager)
) -> APIResponseSchema:
    """Reloads a specific plugin by its name."""
    if not manager:
        raise HTTPException(status_code=503, detail="Plugin manager not initialized")
    try:
        await manager.reload_plugin(plugin_name)
        logger.info("Plugin reloaded", plugin=plugin_name, user_id=str(current_user.id))
        return APIResponseSchema(message=f"Plugin '{plugin_name}' reloaded successfully")
    except PluginNotFoundException as e:
        logger.warning("Plugin reload failed: not found", plugin=plugin_name, user_id=str(current_user.id))
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.error("Plugin reload failed", plugin=plugin_name, error=str(e), user_id=str(current_user.id))
        raise HTTPException(status_code=500, detail=f"Failed to reload plugin: {str(e)}")

@router.delete("/{plugin_name}", response_model=APIResponseSchema)
async def unload_plugin(
    plugin_name: str, 
    current_user: User = Depends(require_admin),
    manager: PluginManager = Depends(get_plugin_manager)
) -> APIResponseSchema:
    """Unloads a specific plugin from memory."""
    if not manager:
        raise HTTPException(status_code=503, detail="Plugin manager not initialized")
    try:
        if await manager.unload_plugin(plugin_name):
            logger.info("Plugin unloaded", plugin=plugin_name, user_id=str(current_user.id))
            return APIResponseSchema(message=f"Plugin '{plugin_name}' unloaded successfully")
        else:
            raise HTTPException(status_code=404, detail=f"Plugin '{plugin_name}' not found or already unloaded")
    except Exception as e:
        logger.error("Plugin unload failed", plugin=plugin_name, error=str(e), user_id=str(current_user.id))
        raise HTTPException(status_code=500, detail=f"Failed to unload plugin: {str(e)}")

@router.get("/registry", summary="List all registered and installed plugins", response_model=APIResponseSchema[List[Dict[str, Any]]])
async def list_all_plugins_status(
    current_user: User = Depends(require_admin),
    manager: PluginManager = Depends(get_plugin_manager)
) -> APIResponseSchema[List[Dict[str, Any]]]:
    """Retrieves a comprehensive list of all plugins, their registration, and installation status."""
    if not manager:
        raise HTTPException(status_code=503, detail="Plugin manager not initialized")
    try:
        all_plugins = await manager.list_all_plugins_status()
        return APIResponseSchema(data=all_plugins, message="Plugin registry and installation statuses retrieved.")
    except Exception as e:
        logger.error("Failed to list all plugins status", error=str(e), user_id=str(current_user.id))
        raise HTTPException(status_code=500, detail="Failed to retrieve plugin statuses.")

@router.get("/{plugin_name}", response_model=APIResponseSchema[Dict[str, Any]])
async def get_plugin_details(
    plugin_name: str, 
    current_user: User = Depends(require_admin),
    manager: PluginManager = Depends(get_plugin_manager)
) -> APIResponseSchema[Dict[str, Any]]:
    """Retrieves detailed information about a specific plugin."""
    if not manager:
        raise HTTPException(status_code=503, detail="Plugin manager not initialized")
    
    details = await manager.get_plugin_details(plugin_name)
    if not details:
        raise HTTPException(status_code=404, detail=f"Plugin '{plugin_name}' not found")
    
    return APIResponseSchema(data=details)

@router.get("/{plugin_name}/config", response_model=APIResponseSchema[Dict[str, Any]])
async def get_plugin_config(
    plugin_name: str, 
    current_user: User = Depends(require_admin),
    manager: PluginManager = Depends(get_plugin_manager)
) -> APIResponseSchema[Dict[str, Any]]:
    """Gets the current configuration and validation schema for a plugin."""
    if not manager:
        raise HTTPException(status_code=503, detail="Plugin manager not initialized")
    try:
        config_info = await manager.get_plugin_config(plugin_name)
        return APIResponseSchema(data=config_info)
    except PluginNotFoundException as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.error("Failed to get plugin config", plugin=plugin_name, error=str(e), user_id=str(current_user.id))
        raise HTTPException(status_code=500, detail=f"Failed to get plugin configuration: {str(e)}")

@router.put("/{plugin_name}/config", response_model=APIResponseSchema[Dict[str, Any]])
async def update_plugin_config(
    plugin_name: str, 
    config_data: Dict[str, Any], 
    current_user: User = Depends(require_admin),
    manager: PluginManager = Depends(get_plugin_manager)
) -> APIResponseSchema[Dict[str, Any]]:
    """Updates a plugin's configuration and applies it via hot reload."""
    if not manager:
        raise HTTPException(status_code=503, detail="Plugin manager not initialized")
    try:
        result = await manager.update_plugin_config(plugin_name, config_data)
        logger.info("Plugin config updated", plugin=plugin_name, user_id=str(current_user.id))
        return APIResponseSchema(data=result)
    except PluginNotFoundException as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.error("Failed to update plugin config", plugin=plugin_name, error=str(e), user_id=str(current_user.id))
        raise HTTPException(status_code=500, detail=f"Failed to update plugin configuration: {str(e)}")

# --- Installation Endpoints (Kept here as they are complex and specific) ---

# --- Installation Endpoints (Refactored to use Service Layer) ---

class InstallRequest(BaseModel):
    github_url: str
    plugin_name: str | None = None

class LocalInstallRequest(BaseModel):
    source_path: str = Field(..., description="The local directory path of the plugin to be installed.")
    plugin_name: str | None = None

class LoadLocalPluginRequest(BaseModel):
    name: str

@router.post("/load", summary="Load a local plugin", response_model=APIResponseSchema)
async def load_local_plugin(
    request: LoadLocalPluginRequest,
    current_user: User = Depends(require_admin),
    manager: PluginManager = Depends(get_plugin_manager)
) -> APIResponseSchema:
    """Loads a plugin that is already present in the 'plugins' directory but is not currently active."""
    if not manager:
        raise HTTPException(status_code=503, detail="Plugin manager not initialized")
    
    try:
        success = await manager.load_local_plugin(request.name)
        if success:
            logger.info("Local plugin loaded successfully", plugin=request.name, user_id=str(current_user.id))
            return APIResponseSchema(message=f"Plugin '{request.name}' loaded successfully.")
        else:
            raise HTTPException(status_code=400, detail=f"Failed to load plugin '{request.name}'. It may not exist or has issues.")
    except PluginNotFoundException as e:
        logger.warning("Local plugin load failed: not found", plugin=request.name, user_id=str(current_user.id))
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.error("Local plugin loading failed with an unexpected error", plugin=request.name, error=str(e), user_id=str(current_user.id))
        raise HTTPException(status_code=500, detail=f"An unexpected error occurred while loading plugin: {str(e)}")


@router.post(
    "/install/start",
    summary="Start or Update a plugin installation",
    response_model=APIResponseSchema[StartInstallationResponse],
)
async def start_plugin_installation(
    request: InstallRequest,
    current_user: User = Depends(require_admin),
    installer: PluginInstallerService = Depends(get_installer_service),
) -> APIResponseSchema[StartInstallationResponse]:
    """
    Starts the secure installation or update process for a plugin from GitHub.
    
    This first stage clones the repo, analyzes its manifest, and compares permissions
    if it's an update. It returns a context ID and a "permission diff" for admin review.
    """
    try:
        result = await installer.start_installation(request.github_url, request.plugin_name)
        logger.info("Plugin installation/update started, awaiting review", github_url=request.github_url, user_id=str(current_user.id))
        return APIResponseSchema(data=result)
    except InstallationError as e:
        logger.error("Plugin installation start failed", github_url=request.github_url, error=e.message, user_id=str(current_user.id))
        raise HTTPException(status_code=400, detail=e.message)
    except Exception as e:
        logger.error("Unexpected error during installation start", github_url=request.github_url, error=str(e), user_id=str(current_user.id))
        raise HTTPException(status_code=500, detail="An unexpected error occurred.")

@router.post(
    "/install/approve",
    summary="Approve permissions for an installation",
    response_model=APIResponseSchema[InstallationStageResponse],
)
async def approve_installation_permissions(
    request: ApprovePermissionsRequest,
    current_user: User = Depends(require_admin),
    installer: PluginInstallerService = Depends(get_installer_service),
) -> APIResponseSchema[InstallationStageResponse]:
    """Approves the permissions for a pending plugin installation."""
    try:
        result = await installer.approve_permissions(request.context_id, request.approved_permissions)
        logger.info("Plugin permissions approved", context_id=request.context_id, user_id=str(current_user.id))
        return APIResponseSchema(data=result)
    except InstallationError as e:
        logger.error("Permission approval failed", context_id=request.context_id, error=e.message, user_id=str(current_user.id))
        raise HTTPException(status_code=400, detail=e.message)

@router.post(
    "/install/resolve",
    summary="Resolve dependencies for an installation",
    response_model=APIResponseSchema[InstallationStageResponse],
)
async def resolve_installation_dependencies(
    request: ResolveDependenciesRequest,
    current_user: User = Depends(require_admin),
    installer: PluginInstallerService = Depends(get_installer_service),
) -> APIResponseSchema[InstallationStageResponse]:
    """Resolves and installs dependencies for a pending plugin installation."""
    try:
        result = await installer.resolve_dependencies(request.context_id, request.strategy)
        logger.info("Plugin dependencies resolved", context_id=request.context_id, user_id=str(current_user.id))
        return APIResponseSchema(data=result)
    except InstallationError as e:
        logger.error("Dependency resolution failed", context_id=request.context_id, error=e.message, user_id=str(current_user.id))
        raise HTTPException(status_code=400, detail=e.message)

@router.post(
    "/install/files",
    summary="Install plugin files",
    response_model=APIResponseSchema[InstallationStageResponse],
)
async def install_plugin_files(
    request: SecureInstallAction,
    current_user: User = Depends(require_admin),
    installer: PluginInstallerService = Depends(get_installer_service),
    manager: PluginManager = Depends(get_plugin_manager),
) -> APIResponseSchema[InstallationStageResponse]:
    """Installs the plugin files into the 'plugins' directory and creates the lock file."""
    try:
        result = await installer.install_plugin_files(request.context_id, manager.plugin_loader)
        logger.info("Plugin files installed", context_id=request.context_id, user_id=str(current_user.id))
        return APIResponseSchema(data=result)
    except InstallationError as e:
        logger.error("Plugin file installation failed", context_id=request.context_id, error=e.message, user_id=str(current_user.id))
        raise HTTPException(status_code=500, detail=e.message)

@router.post(
    "/install/finish",
    summary="Finalize plugin installation",
    response_model=APIResponseSchema[FinishInstallationResponse],
)
async def finish_plugin_installation(
    request: SecureInstallAction,
    current_user: User = Depends(require_admin),
    installer: PluginInstallerService = Depends(get_installer_service),
    manager: PluginManager = Depends(get_plugin_manager),
) -> APIResponseSchema[FinishInstallationResponse]:
    """Finalizes the plugin installation, loads it, and cleans up."""
    try:
        result = await installer.initialize_and_cleanup(request.context_id, manager.plugin_loader, str(current_user.id))
        logger.info("Plugin installation finished", context_id=request.context_id, plugin_name=result.get("plugin_name"), user_id=str(current_user.id))
        return APIResponseSchema(data=result)
    except InstallationError as e:
        logger.error("Plugin finalization failed", context_id=request.context_id, error=e.message, user_id=str(current_user.id))
        raise HTTPException(status_code=500, detail=e.message)

@router.delete("/{plugin_name}/uninstall", summary="Uninstall a plugin completely", response_model=APIResponseSchema)
async def uninstall_plugin_endpoint(
    plugin_name: str, 
    current_user: User = Depends(require_admin),
    manager: PluginManager = Depends(get_plugin_manager)
) -> APIResponseSchema:
    """Completely uninstalls a plugin, removing its files and unregistering it."""
    if not manager:
        raise HTTPException(status_code=503, detail="Plugin manager not initialized")
    try:
        await manager.uninstall_plugin(plugin_name)
        logger.info("Plugin uninstalled successfully", plugin=plugin_name, user_id=str(current_user.id))
        return APIResponseSchema(message=f"Plugin '{plugin_name}' was uninstalled.")
    except PluginNotFoundException as e:
        logger.warning("Uninstall failed, plugin not found", plugin=plugin_name, user_id=str(current_user.id))
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.error("Plugin uninstall failed", plugin=plugin_name, error=str(e), user_id=str(current_user.id))
        raise HTTPException(status_code=500, detail=f"Uninstallation failed: {str(e)}")

@router.post(
    "/update",
    summary="Update a plugin from GitHub",
    response_model=APIResponseSchema[StartInstallationResponse],
)
async def update_plugin_from_github(
    request: InstallRequest,
    current_user: User = Depends(require_admin),
    installer: PluginInstallerService = Depends(get_installer_service),
) -> APIResponseSchema[StartInstallationResponse]:
    """
    Updates a plugin by reinstalling it from its GitHub repository.
    
    This is an alias for the installation process. The installer service's
    internal logic handles backing up the old version, calculating a permission
    diff, and waiting for admin approval before proceeding.
    """
    logger.info("Plugin update initiated as re-installation", github_url=request.github_url, user_id=str(current_user.id))
    # The start_plugin_installation endpoint now handles the full update logic.
    return await start_plugin_installation(request, current_user, installer)

@router.post(
    "/install/from-local",
    summary="Start a plugin installation from a local directory",
    response_model=APIResponseSchema[StartInstallationResponse],
)
async def start_local_plugin_installation(
    request: LocalInstallRequest,
    current_user: User = Depends(require_admin),
    installer: PluginInstallerService = Depends(get_installer_service),
) -> APIResponseSchema[StartInstallationResponse]:
    """
    Starts the secure installation process for a plugin from a local directory.
    This is useful for development or installing plugins from trusted local sources.
    """
    try:
        result = await installer.start_local_installation(request.source_path, request.plugin_name)
        logger.info("Local plugin installation started, awaiting review", source_path=request.source_path, user_id=str(current_user.id))
        return APIResponseSchema(data=result)
    except InstallationError as e:
        logger.error("Local plugin installation start failed", source_path=request.source_path, error=e.message, user_id=str(current_user.id))
        raise HTTPException(status_code=400, detail=e.message)
    except Exception as e:
        logger.error("Unexpected error during local installation start", source_path=request.source_path, error=str(e), user_id=str(current_user.id))
        raise HTTPException(status_code=500, detail="An unexpected error occurred during local installation start.")