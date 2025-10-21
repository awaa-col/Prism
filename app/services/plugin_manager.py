# -*- coding: utf-8 -*-
"""
Service layer for managing plugins.
This service encapsulates all business logic related to plugin management,
including listing, installation, configuration, and lifecycle.
"""
from typing import List, Dict, Any

import os
import yaml
from typing import List, Dict, Any, Optional

from app.core.structured_logging import get_logger
from app.plugins.loader import PluginLoader, PluginNotFoundException
from app.plugins.interface import PluginInterface
from app.utils.plugin_registry import PluginRegistry
import shutil
from pathlib import Path

logger = get_logger("services.plugin_manager")


class PluginManager:
    """
    A service to manage all plugin-related operations.
    It acts as a clean facade over the PluginLoader and other utils.
    """

    def __init__(self, plugin_loader: PluginLoader):
        if not isinstance(plugin_loader, PluginLoader):
            raise TypeError("plugin_loader must be an instance of PluginLoader")
        self.plugin_loader = plugin_loader
        self.registry = PluginRegistry()

    async def list_all_plugins_status(self) -> List[Dict[str, Any]]:
        """
        Lists all plugins, both registered and installed, and their statuses.
        For meta-plugins, includes subplugin information.
        """
        registered_plugins = await self.registry.list_plugins()
        
        installed_plugin_names = set()
        plugin_children = {}  # 存储元插件及其子插件
        
        plugins_dir = Path("plugins")
        if plugins_dir.exists():
            for d in plugins_dir.iterdir():
                # Exclude internal dirs (starting with _) and .backups
                if d.is_dir() and not d.name.startswith("_") and d.name != ".backups":
                    installed_plugin_names.add(d.name)
                    
                    # 检查是否是元插件 (有 group.yml)
                    group_file = d / "group.yml"
                    if group_file.exists():
                        try:
                            with open(group_file, 'r', encoding='utf-8') as f:
                                group_config = yaml.safe_load(f) or {}
                                subplugins = group_config.get('subplugins', [])
                                if subplugins:
                                    plugin_children[d.name] = subplugins
                        except Exception as e:
                            logger.warning(f"Failed to read group.yml for {d.name}", error=str(e))

        result = []
        # Process registered plugins
        for name, info in registered_plugins.items():
            plugin_info = {
                "name": name, 
                "github_url": info.get("github_url"), 
                "installed_at": info.get("installed_at"), 
                "metadata": info.get("metadata", {}), 
                "registry_status": "registered",
                "installation_status": "installed" if name in installed_plugin_names else "not_found",
                "subplugins": plugin_children.get(name, [])  # 添加子插件列表
            }
            result.append(plugin_info)
        
        # Add installed but unregistered plugins
        unregistered_installed = installed_plugin_names - set(registered_plugins.keys())
        for name in unregistered_installed:
            result.append({
                "name": name, 
                "registry_status": "unregistered", 
                "installation_status": "installed",
                "subplugins": plugin_children.get(name, [])
            })
            
        return result

    async def uninstall_plugin(self, plugin_name: str):
        """
        Uninstalls a plugin completely by removing its files and unregistering it.
        """
        plugin_path = Path("plugins") / plugin_name
        if not plugin_path.exists():
            raise PluginNotFoundException(f"Plugin '{plugin_name}' directory not found, cannot uninstall.")
        
        # First, unload if it's loaded
        try:
            await self.plugin_loader.unload_plugin(plugin_name)
            logger.info(f"Unloaded plugin '{plugin_name}' before uninstalling.")
        except Exception as e:
            logger.warning(f"Could not unload plugin '{plugin_name}' before uninstalling (it may not have been loaded). Error: {e}")

        # Remove files and unregister
        shutil.rmtree(plugin_path, ignore_errors=True)
        await self.registry.unregister_plugin(plugin_name)
        logger.info(f"Successfully uninstalled plugin '{plugin_name}'.")

    async def list_plugins(self) -> List[Dict[str, Any]]:
        """List all loaded plugins with their essential metadata."""
        plugins = self.plugin_loader.get_all_plugins()
        plugin_list = []

        for name, plugin in plugins.items():
            try:
                metadata = plugin.get_metadata()
                plugin_info = {
                    "name": name,
                    "version": metadata.version,
                    "description": metadata.description,
                    "author": metadata.author,
                    "status": "active"
                }
            except Exception as e:
                logger.warning(f"Could not retrieve metadata for plugin '{name}'.", error=str(e))
                plugin_info = {"name": name, "status": "error", "error": str(e)}
            plugin_list.append(plugin_info)
        return plugin_list

    async def get_plugin_details(self, plugin_name: str) -> Optional[Dict[str, Any]]:
        """Get detailed information about a specific plugin."""
        plugin = self.plugin_loader.get_plugin(plugin_name)
        metadata = self.plugin_loader.get_plugin_metadata(plugin_name)
        if not plugin or not metadata:
            return None

        try:
            models = await plugin.list_models()
            model_list = [{"id": m.id, "name": m.name} for m in models]
        except Exception:
            model_list = []

        security_info = self.plugin_loader.validate_plugin_security(plugin_name)

        return {
            "name": metadata.name, "version": metadata.version, "description": metadata.description,
            "author": metadata.author, "dependencies": metadata.dependencies,
            "permissions": [{"type": p.type if isinstance(p.type, str) else p.type.value, "resource": p.resource, "description": p.description, "required": p.required} for p in metadata.permissions],
            "models": model_list, "security": security_info, "status": "active"
        }

    async def reload_plugin(self, plugin_name: str) -> None:
        """Reload a specific plugin."""
        await self.plugin_loader.reload_plugin(plugin_name)

    async def unload_plugin(self, plugin_name: str) -> bool:
        """Unload a specific plugin."""
        return await self.plugin_loader.unload_plugin(plugin_name)

    async def load_local_plugin(self, plugin_name: str) -> bool:
        """
        Loads a plugin that already exists locally in the plugins directory.
        Returns True on success.
        """
        logger.info(f"Attempting to load local plugin: {plugin_name}")
        # The core logic is already in the loader
        plugin_instance = await self.plugin_loader.load_plugin(plugin_name)
        return plugin_instance is not None

    async def get_plugin_config(self, plugin_name: str) -> Dict[str, Any]:
        """Get plugin configuration and schema."""
        plugin = self.plugin_loader.get_plugin(plugin_name)
        if not plugin:
            raise PluginNotFoundException(f"Plugin '{plugin_name}' not found")

        current_config = {}
        if hasattr(plugin, 'get_config'):
            try:
                import asyncio
                config_value = plugin.get_config()
                if asyncio.iscoroutine(config_value):
                    current_config = await config_value
                else:
                    current_config = config_value
            except Exception as e:
                logger.warning(f"Failed to get config from instance for {plugin_name}: {e}")

        if not current_config:
            config_file = f"plugins/{plugin_name}/config.yml"
            if os.path.exists(config_file):
                with open(config_file, 'r', encoding='utf-8') as f:
                    current_config = yaml.safe_load(f) or {}

        config_fields = []
        if hasattr(plugin, 'get_required_config_fields'):
            try:
                config_fields = plugin.get_required_config_fields()
            except Exception as e:
                logger.warning(f"Failed to get config fields for {plugin_name}: {e}")

        return {
            "plugin_name": plugin_name,
            "current_config": current_config,
            "config_fields": config_fields,
            "has_config_file": os.path.exists(f"plugins/{plugin_name}/config.yml"),
            "supports_hot_update": hasattr(plugin, 'update_config')
        }

    async def update_plugin_config(self, plugin_name: str, config_data: Dict[str, Any]) -> Dict[str, Any]:
        """Update plugin configuration with hot reload."""
        plugin = self.plugin_loader.get_plugin(plugin_name)
        if not plugin:
            raise PluginNotFoundException(f"Plugin '{plugin_name}' not found")

        update_success = False
        if hasattr(plugin, 'update_config'):
            try:
                if await plugin.update_config(config_data):
                    update_success = True
            except Exception as e:
                logger.warning("Plugin hot-update failed", plugin=plugin_name, error=str(e))

        config_file = f"plugins/{plugin_name}/config.yml"
        os.makedirs(f"plugins/{plugin_name}", exist_ok=True)
        with open(config_file, 'w', encoding='utf-8') as f:
            yaml.dump(config_data, f, default_flow_style=False, allow_unicode=True)

        result = {"message": "Configuration updated", "hot_updated": update_success, "file_saved": True}
        if hasattr(plugin, 'get_config'):
            try:
                import asyncio
                config_value = plugin.get_config()
                if asyncio.iscoroutine(config_value):
                    result["current_config"] = await config_value
                else:
                    result["current_config"] = config_value
            except: pass
        return result

# A dependency provider for FastAPI
_plugin_manager_instance: Optional["PluginManager"] = None

def get_plugin_manager() -> "PluginManager":
    """
    FastAPI dependency to get the singleton instance of PluginManager.
    Raises an exception if the manager is not initialized.
    """
    if _plugin_manager_instance is None:
        # This indicates a programming error. The manager should be initialized at startup.
        raise RuntimeError("PluginManager has not been initialized.")
    return _plugin_manager_instance

def initialize_plugin_manager(loader: PluginLoader):
    """
    Initializes the singleton instance of the PluginManager.
    This should be called once during application startup.
    """
    global _plugin_manager_instance
    if _plugin_manager_instance is not None:
        logger.warning("PluginManager is already initialized. Overwriting instance.")
    
    logger.info("Initializing PluginManager service.")
    _plugin_manager_instance = PluginManager(plugin_loader=loader)