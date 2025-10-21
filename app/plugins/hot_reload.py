"""
Plugin hot reload system.
插件热重载系统，支持文件监控和自动重载。
"""

import asyncio
import os
from typing import Dict, Set, Optional, Callable
from pathlib import Path
import hashlib
from dataclasses import dataclass, field
from datetime import datetime

from app.core.config import get_settings
from app.core.structured_logging import get_logger

settings = get_settings()
logger = get_logger("plugin.hot_reload")


@dataclass
class FileInfo:
    """文件信息"""
    path: Path
    mtime: float
    size: int
    hash: Optional[str] = None


@dataclass
class PluginFileState:
    """插件文件状态"""
    plugin_name: str
    files: Dict[str, FileInfo] = field(default_factory=dict)
    last_check: datetime = field(default_factory=datetime.now)
    
    def has_changed(self, other: 'PluginFileState') -> bool:
        """检查文件是否发生变化"""
        if set(self.files.keys()) != set(other.files.keys()):
            return True
        
        for file_path, file_info in self.files.items():
            other_info = other.files.get(file_path)
            if not other_info:
                return True
            
            # 检查修改时间和大小
            if file_info.mtime != other_info.mtime or file_info.size != other_info.size:
                return True
            
            # 如果启用了哈希检查
            if file_info.hash and other_info.hash and file_info.hash != other_info.hash:
                return True
        
        return False


class PluginFileWatcher:
    """
    插件文件监控器
    
    监控插件目录的文件变化，支持自动重载。
    """
    
    def __init__(self, plugin_loader, use_hash: bool = False):
        self.plugin_loader = plugin_loader
        self.use_hash = use_hash
        self.plugin_states: Dict[str, PluginFileState] = {}
        self.watch_extensions = {'.py', '.json', '.yaml', '.yml'}
        self.ignore_patterns = {'__pycache__', '.pyc', '.pyo', '.git', '.svn'}
        self._watch_task: Optional[asyncio.Task] = None
        self._callbacks: Dict[str, Callable] = {}
        self.enabled = getattr(settings.plugins, 'hot_reload', False)
        self.check_interval = getattr(settings.plugins, 'hot_reload_interval', 2.0)
    
    async def start(self):
        """启动文件监控"""
        if not self.enabled:
            logger.info("Hot reload is disabled")
            return
        
        logger.info("Starting plugin file watcher", interval=self.check_interval)
        
        # 初始化插件状态
        await self._update_all_states()
        
        # 启动监控任务
        self._watch_task = asyncio.create_task(self._watch_loop())
    
    async def stop(self):
        """停止文件监控"""
        if self._watch_task:
            self._watch_task.cancel()
            try:
                await self._watch_task
            except asyncio.CancelledError:
                pass
        
        logger.info("Plugin file watcher stopped")
    
    def register_callback(self, event: str, callback: Callable):
        """注册事件回调"""
        self._callbacks[event] = callback
    
    async def _watch_loop(self):
        """监控循环"""
        while True:
            try:
                await asyncio.sleep(self.check_interval)
                await self._check_changes()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in watch loop: {e}")
    
    async def _check_changes(self):
        """检查文件变化"""
        current_states = await self._get_all_states()
        
        for plugin_name, current_state in current_states.items():
            old_state = self.plugin_states.get(plugin_name)
            
            if old_state and old_state.has_changed(current_state):
                logger.info(f"Detected changes in plugin: {plugin_name}")
                
                # 触发重载前回调
                if 'before_reload' in self._callbacks:
                    await self._callbacks['before_reload'](plugin_name)
                
                # 重载插件
                success = await self._reload_plugin(plugin_name)
                
                # 触发重载后回调
                if 'after_reload' in self._callbacks:
                    await self._callbacks['after_reload'](plugin_name, success)
            
            # 更新状态
            self.plugin_states[plugin_name] = current_state
    
    async def _reload_plugin(self, plugin_name: str) -> bool:
        """重载插件"""
        try:
            logger.info(f"Reloading plugin: {plugin_name}")
            
            # 保存插件配置（如果有）
            plugin = self.plugin_loader.get_plugin(plugin_name)
            saved_config = None
            if plugin and hasattr(plugin, 'get_config'):
                try:
                    saved_config = await plugin.get_config()
                except Exception as e:
                    logger.warning(f"Failed to save plugin config: {e}")
            
            # 重载插件
            success = await self.plugin_loader.reload_plugin(plugin_name)
            
            if success:
                # 恢复配置
                if saved_config:
                    new_plugin = self.plugin_loader.get_plugin(plugin_name)
                    if new_plugin and hasattr(new_plugin, 'set_config'):
                        try:
                            await new_plugin.set_config(saved_config)
                        except Exception as e:
                            logger.warning(f"Failed to restore plugin config: {e}")
                
                logger.info(f"Successfully reloaded plugin: {plugin_name}")
            else:
                logger.error(f"Failed to reload plugin: {plugin_name}")
            
            return success
            
        except Exception as e:
            logger.error(f"Error reloading plugin {plugin_name}: {e}")
            return False
    
    async def _update_all_states(self):
        """更新所有插件状态"""
        self.plugin_states = await self._get_all_states()
    
    async def _get_all_states(self) -> Dict[str, PluginFileState]:
        """获取所有插件的文件状态"""
        states = {}
        
        plugin_dir = self.plugin_loader.plugin_dir
        if not plugin_dir.exists():
            return states
        
        for plugin_path in plugin_dir.iterdir():
            if plugin_path.is_dir() and not plugin_path.name.startswith('_'):
                state = await self._get_plugin_state(plugin_path)
                if state:
                    states[plugin_path.name] = state
        
        return states
    
    async def _get_plugin_state(self, plugin_path: Path) -> Optional[PluginFileState]:
        """获取单个插件的文件状态"""
        try:
            state = PluginFileState(plugin_name=plugin_path.name)
            
            # 递归扫描插件目录
            for file_path in self._scan_directory(plugin_path):
                file_info = await self._get_file_info(file_path)
                if file_info:
                    relative_path = file_path.relative_to(plugin_path)
                    state.files[str(relative_path)] = file_info
            
            return state if state.files else None
            
        except Exception as e:
            logger.error(f"Error getting plugin state for {plugin_path.name}: {e}")
            return None
    
    def _scan_directory(self, directory: Path) -> Set[Path]:
        """扫描目录获取所有相关文件"""
        files = set()
        
        for item in directory.rglob('*'):
            # 忽略特定模式
            if any(pattern in str(item) for pattern in self.ignore_patterns):
                continue
            
            # 只关注特定扩展名的文件
            if item.is_file() and item.suffix in self.watch_extensions:
                files.add(item)
        
        return files
    
    async def _get_file_info(self, file_path: Path) -> Optional[FileInfo]:
        """获取文件信息"""
        try:
            stat = file_path.stat()
            info = FileInfo(
                path=file_path,
                mtime=stat.st_mtime,
                size=stat.st_size
            )
            
            # 如果启用哈希检查
            if self.use_hash:
                info.hash = await self._calculate_file_hash(file_path)
            
            return info
            
        except Exception as e:
            logger.error(f"Error getting file info for {file_path}: {e}")
            return None
    
    async def _calculate_file_hash(self, file_path: Path) -> str:
        """计算文件哈希"""
        try:
            # 在异步环境中计算文件哈希
            loop = asyncio.get_event_loop()
            return await loop.run_in_executor(None, self._hash_file, file_path)
        except Exception as e:
            logger.error(f"Error calculating file hash: {e}")
            return ""
    
    def _hash_file(self, file_path: Path) -> str:
        """同步计算文件哈希"""
        hasher = hashlib.md5()
        with open(file_path, 'rb') as f:
            for chunk in iter(lambda: f.read(4096), b''):
                hasher.update(chunk)
        return hasher.hexdigest()
    
    async def force_reload(self, plugin_name: str) -> bool:
        """强制重载指定插件"""
        return await self._reload_plugin(plugin_name)
    
    def get_plugin_state(self, plugin_name: str) -> Optional[PluginFileState]:
        """获取插件当前状态"""
        return self.plugin_states.get(plugin_name)


class HotReloadManager:
    """
    热重载管理器
    
    提供插件热重载的高级功能。
    """
    
    def __init__(self, plugin_loader):
        self.plugin_loader = plugin_loader
        self.file_watcher = PluginFileWatcher(plugin_loader)
        self.reload_history: Dict[str, list] = {}  # 插件重载历史
        self.reload_lock = asyncio.Lock()
        
        # 注册回调
        self.file_watcher.register_callback('before_reload', self._before_reload)
        self.file_watcher.register_callback('after_reload', self._after_reload)
    
    async def start(self):
        """启动热重载管理器"""
        await self.file_watcher.start()
        logger.info("Hot reload manager started")
    
    async def stop(self):
        """停止热重载管理器"""
        await self.file_watcher.stop()
        logger.info("Hot reload manager stopped")
    
    async def _before_reload(self, plugin_name: str):
        """重载前处理"""
        logger.info(f"Preparing to reload plugin: {plugin_name}")
        
        # 记录重载事件
        if plugin_name not in self.reload_history:
            self.reload_history[plugin_name] = []
        
        self.reload_history[plugin_name].append({
            'timestamp': datetime.now(),
            'status': 'started'
        })
    
    async def _after_reload(self, plugin_name: str, success: bool):
        """重载后处理"""
        status = 'success' if success else 'failed'
        logger.info(f"Plugin reload {status}: {plugin_name}")
        
        # 更新重载历史
        if plugin_name in self.reload_history and self.reload_history[plugin_name]:
            self.reload_history[plugin_name][-1]['status'] = status
            self.reload_history[plugin_name][-1]['end_time'] = datetime.now()
    
    async def reload_plugin(self, plugin_name: str) -> bool:
        """手动重载插件"""
        async with self.reload_lock:
            return await self.file_watcher.force_reload(plugin_name)
    
    async def reload_all(self) -> Dict[str, bool]:
        """重载所有插件"""
        results = {}
        
        async with self.reload_lock:
            for plugin_name in self.plugin_loader.plugins.keys():
                results[plugin_name] = await self.file_watcher.force_reload(plugin_name)
        
        return results
    
    def get_reload_history(self, plugin_name: Optional[str] = None) -> Dict[str, list]:
        """获取重载历史"""
        if plugin_name:
            return {plugin_name: self.reload_history.get(plugin_name, [])}
        return self.reload_history
    
    def get_watcher_status(self) -> Dict[str, any]:
        """获取监控器状态"""
        return {
            'enabled': self.file_watcher.enabled,
            'interval': self.file_watcher.check_interval,
            'watched_plugins': list(self.file_watcher.plugin_states.keys()),
            'total_files': sum(
                len(state.files) 
                for state in self.file_watcher.plugin_states.values()
            )
        }