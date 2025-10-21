# -*- coding: utf-8 -*-
"""
此模块基于 Python 的审计钩子（Audit Hooks）实现了一个强制性的插件沙箱环境。

它通过在解释器层面监控敏感操作，确保插件的行为严格遵守其声明的权限，
解决了传统权限检查可被绕过的问题。
"""
import sys
import os
from contextvars import ContextVar
from typing import List, Any, Optional, Dict, Set, Generator
from contextlib import contextmanager

from app.plugins.interface import SandboxPermission, SandboxPermissionManager
from app.core.structured_logging import get_logger
from app.core.permission_engine import get_permission_engine, PermissionEngine


class PluginContext:
    """
    一个封装了 ContextVar 的辅助类，提供了更方便的上下文管理方法。
    这玩意儿就是为了让我能用 with current_plugin_context.use("plugin_name") 这种潇洒的写法。
    """
    def __init__(self, name: str, default: Any = None):
        self._var: ContextVar[Optional[str]] = ContextVar(name, default=default)

    def get(self) -> Optional[str]:
        """获取当前上下文的值"""
        return self._var.get()

    @contextmanager
    def use(self, value: str) -> Generator[None, None, None]:
        """设置上下文并在 with 块结束时自动重置"""
        token = self._var.set(value)
        try:
            yield
        finally:
            self._var.reset(token)

# 使用我自定义的 PluginContext 来追踪当前正在执行的插件上下文
current_plugin_context = PluginContext("current_plugin_context", default=None)

logger = get_logger("audit_sandbox")

class AuditHookManager:
    """
    管理全局审计钩子，实现插件沙箱。
    这玩意儿是个单例，整个应用生命周期里只应该有一个实例。
    """
    _instance = None
    
    def __new__(cls, *args, **kwargs):
        if not cls._instance:
            cls._instance = super(AuditHookManager, cls).__new__(cls)
        return cls._instance

    def __init__(self, permission_manager: Optional[SandboxPermissionManager] = None):
        # 防止重复初始化
        if hasattr(self, '_initialized') and self._initialized:
            return
        
        if permission_manager is None:
            # 在没有提供管理器的情况下，无法进行权限检查。
            # 这在测试或非插件执行环境中可能是正常的。
            logger.warning("AuditHookManager initialized without a permission manager. No permission checks will be performed.")
            self.permission_manager = None
        else:
            self.permission_manager = permission_manager
            
        self.is_active = False
        self._initialized = True
        # 插件根目录路径映射：用于强制目录限制
        self.plugin_root_paths: Dict[str, str] = {}
        # 获取权限引擎实例
        self.permission_engine: PermissionEngine = get_permission_engine()
        logger.info("AuditHookManager initialized with PermissionEngine.")

    def activate(self):
        """
        激活审计钩子。
        一旦激活，所有受监控的事件都将被检查。
        """
        if self.is_active:
            logger.warning("Audit hook is already active.")
            return
        
        if self.permission_manager is None:
            logger.error("Cannot activate audit hook without a permission manager.")
            raise RuntimeError("AuditHookManager requires a SandboxPermissionManager to be activated.")
            
        sys.addaudithook(self._audit_hook)
        self.is_active = True
        logger.info("Audit hook has been activated. Sandboxing is now enforced.")

    def set_plugin_root_paths(self, plugin_root_paths: Dict[str, str]) -> None:
        """
        设置插件根目录路径映射。
        这是三层安全架构的第一层：监狱围墙。
        """
        self.plugin_root_paths = plugin_root_paths.copy()
        logger.info(f"Updated plugin root paths for {len(plugin_root_paths)} plugins")

    def _audit_hook(self, event: str, args: tuple[Any, ...]):
        """
        核心审计钩子函数。这是我们安插在解释器里的间谍。
        它会根据事件类型和插件上下文，进行精确的权限检查。
        """
        plugin_name = current_plugin_context.get()
        if not plugin_name:
            return  # 非插件操作，直接放行

        # 新的逻辑：使用权限引擎进行匹配和检查
        # 核心安全边界检查（如锁文件保护）仍然保留
        if event == 'open':
            path_arg, mode, flags = args
            if self._is_lock_file_access(plugin_name, path_arg, mode, flags):
                # _is_lock_file_access 内部会抛出异常
                return
        elif event in ['os.remove', 'os.unlink', 'os.rename']:
             if self._is_lock_file_modification(plugin_name, event, args):
                 return

        # 将事件映射到所需的权限
        required_perms = self.permission_engine.map_event_to_permissions(event, args)

        if not required_perms:
            # 如果没有匹配到任何需要检查的权限，则直接放行
            # (例如，非敏感的socket操作)
            return

        # --- 三层安全检查的第一层：监狱围墙 ---
        # 这一层依然重要，作为独立于权限声明的基础安全保障
        # 注意：这里的检查现在主要针对文件路径的合法性，而不是决定权限名称
        if event == 'open':
            path_arg, *_ = args
            try:
                self._enforce_directory_jail(plugin_name, str(path_arg))
            except PermissionError as e:
                # 如果目录限制检查失败，记录并重新抛出异常
                violation_message = str(e)
                if self.permission_manager:
                    self.permission_manager.log_violation(plugin_name, violation_message)
                logger.warning(violation_message)
                raise
        
        # 检查所有匹配到的权限
        # 这里的逻辑是“或”：只要插件拥有其中一个权限即可
        # 在更复杂的场景下，可能需要“与”逻辑
        has_any_permission = False
        checked_perms = []
        for perm_def, resource in required_perms:
            checked_perms.append(perm_def.name)
            if self.permission_manager and self.permission_manager.check_permission(plugin_name, perm_def.name, resource):
                has_any_permission = True
                break # 只要有一个权限匹配，就通过

        if not has_any_permission:
            violation_message = (
                f"Plugin '{plugin_name}' blocked from performing unauthorized action. "
                f"Event: {event}, Required one of Permissions: {checked_perms}, Resource: {args[0] if args else 'N/A'}"
            )
            if self.permission_manager:
                self.permission_manager.log_violation(plugin_name, violation_message)
            logger.warning(violation_message)
            raise PermissionError(violation_message)


    def _is_lock_file_access(self, plugin_name: str, path_arg: Any, mode: Optional[str], flags: Optional[int]) -> bool:
        """检查是否正在访问任何锁文件，如果是则抛出异常。"""
        is_write = False
        if isinstance(mode, str):
            is_write = any(c in mode for c in ('w', 'a', '+'))
        elif isinstance(flags, int):
            # Check for write flags for os.open()
            if (flags & os.O_WRONLY) or (flags & os.O_RDWR) or (flags & os.O_APPEND):
                is_write = True
        
        try:
            abs_path = os.path.abspath(str(path_arg))
            project_root = os.getcwd()
            
            # 检查 system_secure 目录
            system_secure_dir = os.path.join(project_root, 'system_secure')
            if abs_path.startswith(os.path.abspath(system_secure_dir) + os.sep):
                violation_message = f"Plugin '{plugin_name}' attempted to access system secure directory: {path_arg}. Access denied!"
                if self.permission_manager:
                    self.permission_manager.log_violation(plugin_name, violation_message)
                logger.warning(violation_message)
                raise PermissionError(violation_message)

            # 检查插件自身的锁文件
            plugin_root = self.plugin_root_paths.get(plugin_name)
            if plugin_root:
                lock_path = os.path.join(os.path.abspath(plugin_root), 'permissions.lock.json')
                if abs_path == lock_path:
                    violation_message = f"Plugin '{plugin_name}' attempted to access its own lock file: {path_arg}"
                    if self.permission_manager:
                        self.permission_manager.log_violation(plugin_name, violation_message)
                    logger.error(violation_message)
                    raise PermissionError(violation_message)

        except Exception as e:
            if isinstance(e, PermissionError):
                raise
            # 路径解析失败时，进行字符串级别的最终检查
            path_str = str(path_arg).lower().replace('\\', '/')
            if 'system_secure/' in path_str or (is_write and 'permissions.lock.json' in path_str):
                 violation_message = f"Plugin '{plugin_name}' attempted suspicious file access (string check): {path_arg}"
                 if self.permission_manager:
                     self.permission_manager.log_violation(plugin_name, violation_message)
                 logger.error(violation_message)
                 raise PermissionError(violation_message)
        
        return False

    def _is_lock_file_modification(self, plugin_name: str, event: str, args: tuple[Any, ...]) -> bool:
        """检查是否通过 os.remove/rename 等方式修改锁文件。"""
        paths_to_check = []
        if event in ['os.remove', 'os.unlink']:
            paths_to_check.append(str(args[0]) if args else '')
        elif event == 'os.rename':
            paths_to_check.append(str(args[0]) if len(args) > 0 else '')
            paths_to_check.append(str(args[1]) if len(args) > 1 else '')
        
        for path in paths_to_check:
            path_lower = path.lower().replace('\\', '/')
            if 'permissions.lock.json' in path_lower:
                violation_message = f"Plugin '{plugin_name}' attempted to modify a lock file via {event}: {path}"
                if self.permission_manager:
                    self.permission_manager.log_violation(plugin_name, violation_message)
                logger.error(violation_message)
                raise PermissionError(violation_message)
        return False

    def _enforce_directory_jail(self, plugin_name: str, path: str):
        """强制执行目录限制（监狱围墙）。"""
        try:
            abs_path = os.path.abspath(path)
            plugin_root = self.plugin_root_paths.get(plugin_name)
            
            if not plugin_root:
                raise PermissionError(f"Plugin '{plugin_name}' root path not registered")
            
            plugin_root_abs = os.path.abspath(plugin_root)
            
            # 检查是否在插件根目录内
            is_within_plugin_dir = abs_path.startswith(plugin_root_abs + os.sep) or abs_path == plugin_root_abs
            
            # 检查是否在允许的临时目录内
            project_root = os.getcwd()
            temp_dir_abs = os.path.abspath(os.path.join(project_root, 'data', 'temp', plugin_name))
            is_within_temp_dir = abs_path.startswith(temp_dir_abs + os.sep) or abs_path == temp_dir_abs

            if is_within_plugin_dir or is_within_temp_dir:
                return # 在允许的目录内，通过检查

            # 如果都不在，则检查是否有系统级权限以允许越界访问
            has_system_privileges = False
            if self.permission_manager:
                # 假设拥有任何以 "system." 开头的权限即视为拥有系统特权
                has_system_privileges = self.permission_manager.has_permission_prefix(plugin_name, "system.")
            
            if not has_system_privileges:
                raise PermissionError(f"Plugin '{plugin_name}' attempted to access path outside its allowed directories: {path}")

        except Exception as e:
            if isinstance(e, PermissionError):
                raise
            # 路径解析失败，为安全起见，拒绝访问
            raise PermissionError(f"Could not resolve path for resource '{path}': {e}")
    
    def _check_permission(self, plugin_name: str, event: str, perm_name: str, resource: Optional[str] = None):
        """
        通用的权限检查逻辑。(已废弃，由新的 _audit_hook 逻辑取代)
        """
        if not self.permission_manager:
            return

        # 我们不再需要 SandboxPermission，直接使用字符串权限名
        if not self.permission_manager.check_permission(plugin_name, perm_name, resource):
            violation_message = (
                f"Plugin '{plugin_name}' blocked from performing unauthorized action. "
                f"Event: {event}, Required Permission: {perm_name}, Resource: {resource or 'N/A'}"
            )
            self.permission_manager.log_violation(plugin_name, violation_message)
            logger.warning(violation_message)
            # 抛出异常，当场阻止操作
            raise PermissionError(violation_message)

def get_audit_manager(permission_manager: Optional[SandboxPermissionManager] = None) -> AuditHookManager:
    """获取 AuditHookManager 的单例实例"""
    return AuditHookManager(permission_manager)