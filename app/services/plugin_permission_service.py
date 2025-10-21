import json
import asyncio
from pathlib import Path
from typing import Dict, Any, Optional
import aiofiles

from app.core.permission_engine import get_permission_engine
from app.core.structured_logging import get_logger

logger = get_logger("services.plugin_permission")

LOCK_FILE_BASE_DIR = Path("plugins")

class PluginPermissionService:
    """
    Service for managing plugin permission lock files asynchronously.
    """

    def _get_lock_file_path(self, plugin_name: str) -> Path:
        """Gets the path to the permission lock file for a specific plugin."""
        return LOCK_FILE_BASE_DIR / plugin_name / "permissions.lock.json"

    async def get_permissions(self, plugin_name: str) -> Dict[str, Any]:
        """Gets the permission list for a plugin asynchronously."""
        lock_file = self._get_lock_file_path(plugin_name)
        if not lock_file.exists():
            raise FileNotFoundError(f"Permission lock file for plugin '{plugin_name}' not found.")
        
        async with aiofiles.open(lock_file, 'r', encoding='utf-8') as f:
            content = await f.read()
            return json.loads(content)

    async def add_permission(self, plugin_name: str, permission_data: Dict[str, Any]) -> Dict[str, Any]:
        """Adds a new permission to a plugin asynchronously."""
        lock_file = self._get_lock_file_path(plugin_name)
        if not lock_file.exists():
            raise FileNotFoundError(f"Cannot add permission: lock file for '{plugin_name}' not found.")

        permission_type = permission_data.get("type")
        if not permission_type:
            raise ValueError("Permission 'type' is a required field.")

        permission_engine = get_permission_engine()
        if not permission_engine.is_valid_permission_type(permission_type):
            raise ValueError(f"Permission type '{permission_type}' is not a valid or known permission.")

        async with aiofiles.open(lock_file, 'r+', encoding='utf-8') as f:
            content = await f.read()
            data = json.loads(content)
            permissions = data.get("permissions", [])
            
            for p in permissions:
                if p.get("type") == permission_data.get("type") and p.get("resource") == permission_data.get("resource"):
                    logger.warning("Permission already exists", plugin=plugin_name, permission=permission_data)
                    return data
            
            permissions.append({
                "type": permission_data["type"],
                "resource": permission_data.get("resource"),
                "description": permission_data.get("description", "")
            })
            
            data["permissions"] = permissions
            data["updated_at"] = str(asyncio.get_event_loop().time())
            
            await f.seek(0)
            await f.truncate()
            await f.write(json.dumps(data, indent=2, ensure_ascii=False))
            
        return data

    async def remove_permission(self, plugin_name: str, permission_type: str, resource: Optional[str] = None) -> Dict[str, Any]:
        """Removes a specific permission from a plugin asynchronously."""
        lock_file = self._get_lock_file_path(plugin_name)
        if not lock_file.exists():
            raise FileNotFoundError(f"Cannot remove permission: lock file for '{plugin_name}' not found.")

        async with aiofiles.open(lock_file, 'r+', encoding='utf-8') as f:
            content = await f.read()
            data = json.loads(content)
            original_permissions = data.get("permissions", [])
            
            permissions_to_keep = [
                p for p in original_permissions
                if not (p.get("type") == permission_type and p.get("resource") == resource)
            ]
            
            if len(permissions_to_keep) == len(original_permissions):
                logger.warning("Permission to remove not found", plugin=plugin_name, type=permission_type, resource=resource)
                return data

            data["permissions"] = permissions_to_keep
            data["updated_at"] = str(asyncio.get_event_loop().time())

            await f.seek(0)
            await f.truncate()
            await f.write(json.dumps(data, indent=2, ensure_ascii=False))

        return data

def get_plugin_permission_service() -> "PluginPermissionService":
    return PluginPermissionService()