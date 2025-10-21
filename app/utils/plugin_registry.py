import json
import asyncio
from typing import Dict, Any, Optional
from pathlib import Path
from app.core.structured_logging import get_logger

class PluginRegistry:
    """插件注册表，管理插件的元数据"""
    
    def __init__(self, registry_file: str = "plugin_data/plugin_registry.json"):
        self.registry_file = Path(registry_file)
        # 确保注册表目录存在
        self.registry_file.parent.mkdir(parents=True, exist_ok=True)
        self.logger = get_logger("plugin_registry")
        
    async def register_plugin(self, plugin_name: str, github_url: Optional[str], metadata: Dict[str, Any]) -> None:
        """注册插件"""
        try:
            registry = await self._load_registry()
            
            registry[plugin_name] = {
                "github_url": github_url,
                "installed_at": str(asyncio.get_event_loop().time()),
                "metadata": metadata
            }
            
            await self._save_registry(registry)
            self.logger.info(f"Registered plugin: {plugin_name}")
            
        except Exception as e:
            self.logger.error(f"Failed to register plugin {plugin_name}: {e}")
    
    async def unregister_plugin(self, plugin_name: str) -> None:
        """取消注册插件"""
        try:
            registry = await self._load_registry()
            
            if plugin_name in registry:
                del registry[plugin_name]
                await self._save_registry(registry)
                self.logger.info(f"Unregistered plugin: {plugin_name}")
            
        except Exception as e:
            self.logger.error(f"Failed to unregister plugin {plugin_name}: {e}")
    
    async def get_plugin_info(self, plugin_name: str) -> Optional[Dict[str, Any]]:
        """获取插件信息"""
        try:
            registry = await self._load_registry()
            return registry.get(plugin_name)
            
        except Exception as e:
            self.logger.error(f"Failed to get plugin info {plugin_name}: {e}")
            return None
    
    async def list_plugins(self) -> Dict[str, Any]:
        """列出所有注册的插件"""
        try:
            return await self._load_registry()
            
        except Exception as e:
            self.logger.error(f"Failed to list plugins: {e}")
            return {}
    
    async def _load_registry(self) -> Dict[str, Any]:
        """加载注册表"""
        if not self.registry_file.exists():
            return {}
        
        try:
            with open(self.registry_file, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            return {}
    
    async def _save_registry(self, registry: Dict[str, Any]) -> None:
        """保存注册表"""
        # 保险：保存前确保目录存在
        self.registry_file.parent.mkdir(parents=True, exist_ok=True)
        with open(self.registry_file, 'w', encoding='utf-8') as f:
            json.dump(registry, f, indent=2, ensure_ascii=False)