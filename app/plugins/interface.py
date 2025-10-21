"""
Plugin interface definition.
"""

from abc import ABC, abstractmethod
from typing import AsyncGenerator, List, Dict, Any, Optional, Set, Callable, TYPE_CHECKING, Awaitable, Coroutine
from dataclasses import dataclass, field
from pathlib import Path

import httpx

from app.core.structured_logging import get_logger

from sqlalchemy.ext.asyncio import AsyncSession

if TYPE_CHECKING:
    from fastapi import APIRouter


@dataclass(frozen=True)
class SandboxPermission:
    """沙箱权限定义，用于控制插件在隔离环境中的能力"""
    type: str
    resource: Optional[str] = None  # 具体资源，如域名、文件路径等
    description: str = ""
    required: bool = True  # 是否必需，False表示可选权限


@dataclass
class PluginMetadata:
    """Plugin metadata"""
    name: str
    version: str
    description: str
    author: str
    requires_auth: bool = True  # 保留以兼容旧插件
    auth_type: str = "api_key"  # 保留以兼容旧插件
    permissions: List[SandboxPermission] = field(default_factory=list)
    dependencies: List[str] = field(default_factory=list)  # 依赖的其他插件
    min_python_version: str = "3.8"
    sandbox_required: bool = True


class SandboxPermissionManager:
    """沙箱权限管理器，由 PluginLoader 在加载时使用"""
    
    def __init__(self):
        # 新的存储结构：plugin_name -> set of permission_names (e.g., "file.read.plugin")
        self.granted_permissions: Dict[str, Set[str]] = {}
        self.permission_violations: Dict[str, List[str]] = {}
        # 引入权限引擎
        from app.core.permission_engine import get_permission_engine
        self.permission_engine = get_permission_engine()
    
    def grant_permissions_from_lock(self, plugin_name: str, lock_data: Dict[str, Any]) -> None:
        """
        从 permissions.lock.json 的内容中授予插件权限。
        这是推荐的、面向新系统的权限授予方式。
        """
        if plugin_name not in self.granted_permissions:
            self.granted_permissions[plugin_name] = set()

        lock_permissions = lock_data.get("permissions", [])
        for p_data in lock_permissions:
            # p_data 的格式是 {'type': 'file', 'resource': 'read', ...}
            # 我们需要将其映射到权限引擎中定义的权限名称
            perm_def = self.permission_engine.find_definition_for_declaration(
                p_data.get("type", ""),
                p_data.get("resource")
            )
            if perm_def:
                self.granted_permissions[plugin_name].add(perm_def.name)
            else:
                # 记录无法识别的权限声明
                # 在生产环境中，这可能表示一个警告或错误
                unmapped_perm = f"{p_data.get('type')}:{p_data.get('resource')}"
                get_logger("permission_manager").warning(
                    f"Permission declaration '{unmapped_perm}' for plugin '{plugin_name}' could not be mapped to a known permission definition."
                )

    def grant_permissions(self, plugin_name: str, permissions: List[SandboxPermission]) -> None:
        """
        授予插件沙箱权限（兼容旧版）。
        此方法现在会将旧的 SandboxPermission 对象转换为新的基于名称的权限。
        """
        if plugin_name not in self.granted_permissions:
            self.granted_permissions[plugin_name] = set()
        
        for permission in permissions:
            # 兼容性转换：尝试将旧的 SandboxPermission 映射到新的权限定义
            perm_def = self.permission_engine.find_definition_for_declaration(
                permission.type,
                permission.resource
            )
            if perm_def:
                self.granted_permissions[plugin_name].add(perm_def.name)

    def check_permission(self, plugin_name: str, required_perm_name: str, resource: Optional[Any] = None) -> bool:
        """
        检查插件是否有指定名称的沙箱权限。
        此方法现在支持基于通配符的资源匹配。
        """
        if plugin_name not in self.granted_permissions:
            return False
        
        granted_perms_for_plugin = self.granted_permissions[plugin_name]

        # 1. 直接检查权限名称是否存在
        if required_perm_name in granted_perms_for_plugin:
            return True

        # 2. 通配符权限检查 (例如, network.domain.*)
        # 这里的逻辑需要权限引擎的辅助来解析通配符
        # 示例：请求 network.domain.api.example.com，检查是否存在 network.domain.*
        for granted_perm_name in granted_perms_for_plugin:
            if self._permission_name_matches(granted_perm_name, required_perm_name):
                 # 如果名称匹配，我们还需要检查资源是否匹配（如果适用）
                 perm_def = self.permission_engine.get_permission_definition(granted_perm_name)
                 if perm_def and perm_def.resource_matcher(resource):
                     return True
        
        return False
    
    def has_permission_prefix(self, plugin_name: str, prefix: str) -> bool:
        """检查插件是否拥有任何以指定前缀开头的权限。"""
        if plugin_name not in self.granted_permissions:
            return False
        
        for granted_perm in self.granted_permissions.get(plugin_name, set()):
            if granted_perm.startswith(prefix):
                return True
        return False

    def _permission_name_matches(self, granted_pattern: str, requested: str) -> bool:
        """
        使用 fnmatch 进行权限名称的通配符匹配。
        例如, "network.domain.*" 应该匹配 "network.domain.example.com"。
        """
        import fnmatch
        return fnmatch.fnmatch(requested, granted_pattern)

    def log_violation(self, plugin_name: str, violation: str) -> None:
        """记录权限违规"""
        if plugin_name not in self.permission_violations:
            self.permission_violations[plugin_name] = []
        
        self.permission_violations[plugin_name].append(violation)
    
    def get_violations(self, plugin_name: str) -> List[str]:
        """获取插件的权限违规记录"""
        return self.permission_violations.get(plugin_name, [])


@dataclass
class RequestContext:
    """
    请求上下文对象，用于在插件调用链中传递状态
    """
    request_data: Dict[str, Any]
    response_data: Dict[str, Any] = field(default_factory=dict)
    user_id: Optional[str] = None
    current_plugin_name: Optional[str] = None # 当前正在执行的插件名
    route: Optional[str] = None # The route path that triggered this context
    is_short_circuited: bool = False  # 中间件可置为True来提前中断流程
    trace_log: Optional[List[str]] = field(default=None, repr=False, init=False) # 执行追踪日志
    
    _shared_state: Dict[str, Any] = field(default_factory=dict, repr=False, init=False)

    def get_user_id(self) -> Optional[str]:
        """获取用户ID"""
        return self.user_id
    
    def set_user_id(self, user_id: str) -> None:
        """设置用户ID"""
        self.user_id = user_id
    
    # --- 统一的状态管理方法 ---
    
    def set(self, key: str, value: Any) -> None:
        """
        在调用链的共享上下文中设置一个状态值。
        这个值对于后续的所有插件都是可见的。
        """
        self._shared_state[key] = value

    def get(self, key: str, default: Any = None) -> Any:
        """
        从调用链的共享上下文中读取一个状态值。
        """
        return self._shared_state.get(key, default)

    def add_trace(self, message: str):
        """如果追踪开启，则添加一条追踪日志。"""
        if self.trace_log is not None:
            # 使用 time.monotonic() 获取单调递增时间，更适合性能分析
            import time
            self.trace_log.append(f"[{time.monotonic():.4f}] {message}")

    # --- 便捷的响应与流程控制方法 ---

    def respond(self, content: Any = None, **extra: Any) -> None:
        """设置成功响应的便捷方法（无副作用，仅写入 response_data）。"""
        self.response_data = {"success": True}
        if content is not None:
            self.response_data["content"] = content
        if extra:
            self.response_data.update(extra)
    
    def error(self, message: str, code: str = "error", short_circuit: bool = True, **extra: Any) -> None:
        """设置错误响应的便捷方法，并可选短路调用链。"""
        self.response_data = {"success": False, "error": message, "code": code}
        if extra:
            self.response_data.update(extra)
        if short_circuit:
            self.is_short_circuited = True
    
    def short_circuit(self, reason: str, code: str = "short_circuit", **extra: Any) -> None:
        """
        主动短路当前调用链并记录原因。
        Args:
            reason: 短路原因描述
            code: 可选错误码，默认 "short_circuit"
            **extra: 额外元数据，将合并进 response_data
        """
        self.response_data = {"success": False, "error": reason, "code": code}
        if extra:
            self.response_data.update(extra)
        self.is_short_circuited = True


class PluginInterface(ABC):
    """
    Abstract base class for all AI model plugins.
    Each plugin must implement this interface.
    """
    
    def __init__(self, http_client: httpx.AsyncClient, permission_manager: Optional[SandboxPermissionManager] = None, **kwargs):
        """
        Initialize plugin with shared HTTP client and permission manager.
        
        Args:
            http_client: Shared async HTTP client for making requests
            permission_manager: Sandbox Permission manager for access control
        """
        self.http_client = http_client
        self.permission_manager = permission_manager
        self._logger = None  # 延迟初始化
        self.plugin_dir: Optional[Path] = None  # 插件目录路径
        
        # 此属性由沙箱环境动态注入，用于IPC通信。在此声明以满足静态类型检查器。
        self._send_ipc_request: Optional[Callable[..., Awaitable[Any]]] = None
    
    @property
    def logger(self):
        """获取logger，延迟初始化"""
        if self._logger is None:
            try:
                plugin_name = self.get_metadata().name
                self._logger = get_logger(f"plugin.{plugin_name}")
            except Exception:
                # 如果无法获取metadata，使用默认logger
                self._logger = get_logger("plugin.unknown")
        return self._logger
    
    @abstractmethod
    def get_metadata(self) -> PluginMetadata:
        """Get plugin metadata"""
        pass
    
    @abstractmethod
    async def initialize(self) -> None:
        """
        Initialize the plugin.
        Called once when the plugin is loaded.
        """
        pass
    
    @abstractmethod
    async def shutdown(self) -> None:
        """
        Cleanup plugin resources.
        Called when the plugin is being unloaded.
        """
        pass
    
    
    def get_router(self) -> Optional['APIRouter']:
        """
        获取插件的API路由。
        
        插件可以通过实现此方法来注册自己的API端点。
        返回的路由会被动态挂载到主应用上。
        
        Returns:
            APIRouter对象，如果插件不需要API路由则返回None
        """
        return None

    def get_route_schema(self) -> Optional[List[Dict[str, Any]]]:
        """
        可选：返回“路由 schema”用于主进程装配路由，避免主进程直接导入插件代码。
        schema 每项示例：
        {
          "method": "POST",
          "path": "/example-route",
          "auth": "user",
          "handler": "handle_example_route",  # 插件内方法，由主进程通过 invoke 调用
          "stream": False
        }
        未提供则主进程不为该插件装配路由。
        """
        # 默认实现：如果插件目录下存在 routes.schema.json 则读取
        try:
            if self.plugin_dir:
                from pathlib import Path
                import json
                schema_path = Path(self.plugin_dir) / "routes.schema.json"
                if schema_path.exists():
                    with open(schema_path, "r", encoding="utf-8") as f:
                        data = json.load(f)
                        if isinstance(data, list):
                            return data
        except Exception:
            pass
        return None

    async def handle(self, context: RequestContext, next_plugin: Optional[Callable[[RequestContext], Coroutine[Any, Any, None]]] = None) -> None:
        """
        处理请求的核心方法。
        
        Args:
            context: 请求上下文对象
            next_plugin: 调用链中下一个插件的回调函数
        """
        # 默认实现：直接调用下一个插件
        if next_plugin:
            await next_plugin(context)

    async def invoke(self, method_name: str, payload: Dict[str, Any]) -> Any:
        """
        Directly invoke a plugin's core logic with a method name and payload.
        This is the primary mechanism for routers to trigger plugin actions.
        The payload is expected to contain everything needed to construct a RequestContext.
        """
        # 默认实现：创建一个 RequestContext 并调用 handle 方法
        # 这使得插件无需为简单的路由实现 invoke，只需实现 handle
        try:
            # 从 payload 中提取创建 RequestContext 所需的数据
            request_data = {
                "body": payload.get("body"),
                "query": payload.get("query"),
                "path_params": payload.get("path_params"),
                "headers": payload.get("headers"),
            }
            
            context = RequestContext(
                request_data=request_data,
                user_id=payload.get("user_id"), # 假设 user_id 也在 payload 中
                current_plugin_name=self.get_metadata().name,
                route=payload.get("route")
            )

            # 调用 handle 方法
            await self.handle(context)
            
            # 从 context 中提取响应
            return context.response_data

        except Exception as e:
            self.logger.error(
                f"Error during default invoke for method '{method_name}'",
                exc_info=True
            )
            return {
                "success": False,
                "error": f"An internal error occurred in plugin '{self.get_metadata().name}': {e}",
                "code": "plugin_invoke_error"
            }

    async def get_db_session(self) -> AsyncGenerator['AsyncSession', None]:
        """
        获取一个数据库会话，前提是插件拥有 DATABASE_ACCESS 权限。
        这是插件访问数据库的唯一合法途径。

        用法:
            async for session in self.get_db_session():
                # ... 使用 session 进行数据库操作 ...
                result = await session.execute(...)
        """
        # 1. 权限检查
        db_permission_name = "database.access"
        plugin_name = self.get_metadata().name
        
        if not self.permission_manager or not self.permission_manager.check_permission(plugin_name, db_permission_name):
            self.logger.error("Plugin attempted to access database without permission.", plugin=plugin_name)
            raise PermissionError(f"Plugin '{plugin_name}' does not have DATABASE_ACCESS permission.")
        
        # 2. 动态导入并提供会话
        #    我们在这里进行动态、局部的导入，以避免在模块顶层暴露 get_db，
        #    同时配合审计钩子阻止插件直接导入 'app.db.session'。
        try:
            from app.db.session import get_db
            from sqlalchemy.ext.asyncio import AsyncSession
        except ImportError as e:
            self.logger.critical("Failed to import database session components.", error=str(e))
            raise RuntimeError("Database session provider is not available.") from e

        # 将 get_db() 的生成器委托出去
        async for session in get_db():
            yield session

    async def get_config(self) -> Dict[str, Any]:
        """Get the current configuration of the plugin"""
        # Default implementation returns empty dict
        return {}

    async def update_config(self, config_data: Dict[str, Any]) -> bool:
        """Update the plugin's configuration with hot reload"""
        # Default implementation does nothing and returns False
        self.logger.warning("Plugin's 'update_config' method was called but not implemented.")
        return False

    def get_required_config_fields(self) -> List[Dict[str, Any]]:
        """Get the required configuration fields for the plugin"""
        # Default implementation returns the config schema
        schema = self.get_config_schema()
        return schema if schema is not None else []

    
    async def _safe_http_request(self, method: str, url: str, **kwargs) -> httpx.Response:
        """
        安全的HTTP请求，会检查网络权限。
        注意：此检查依赖于 PluginLoader 授予的沙箱权限，而非用户访问权限。
        
        Args:
            method: HTTP方法
            url: 请求URL
            **kwargs: 其他参数
            
        Returns:
            HTTP响应
            
        Raises:
            PermissionError: 如果没有网络权限
        """
        from urllib.parse import urlparse
        
        parsed_url = urlparse(url)
        scheme = parsed_url.scheme.lower()
        domain = parsed_url.netloc
        
        # 检查协议权限
        if scheme == "http":
            permission_type = "network.http"
        elif scheme == "https":
            permission_type = "network.https"
        else:
            raise PermissionError(f"Unsupported protocol: {scheme}")

        # 检查特定域名权限
        domain_permission_name = f"network.domain.{domain}"
        # 检查通用协议权限
        protocol_permission_name = permission_type

        has_domain_perm = self.permission_manager and self.permission_manager.check_permission(self.get_metadata().name, domain_permission_name)
        has_protocol_perm = self.permission_manager and self.permission_manager.check_permission(self.get_metadata().name, protocol_permission_name)

        if not (has_domain_perm or has_protocol_perm):
            raise PermissionError(f"Plugin does not have permission for {permission_type} on {domain}")
        
        # 执行请求
        return await self.http_client.request(method, url, **kwargs) 
    
    async def save_config(self, config_data: Dict[str, Any]) -> bool:
        """
        保存插件配置数据到中央数据区。
        
        插件调用此方法时，数据会通过进程间通信发送到主进程，
        然后由主进程负责将其持久化到 data/{plugin_name}.json。
        
        Args:
            config_data: 要保存的配置数据字典
            
        Returns:
            True if save was successful
            
        Raises:
            PermissionError: 如果没有 CONFIG_DATA_WRITE 权限
        """
        # 检查写入权限
        write_permission_name = "config.data_write"
        if not self.permission_manager or not self.permission_manager.check_permission(self.get_metadata().name, write_permission_name):
            raise PermissionError("Plugin does not have permission to write configuration.")
        
        # 如果是在沙箱中运行，通过IPC发送保存请求
        if hasattr(self, '_send_ipc_request') and self._send_ipc_request is not None:
            return await self._send_ipc_request("save_config", config_data)
        else:
            # 非沙箱环境的fallback（主要用于测试）
            import json
            from pathlib import Path
            plugin_name = self.get_metadata().name
            data_file = Path("data") / f"{plugin_name}.json"
            data_file.parent.mkdir(exist_ok=True)
            with open(data_file, 'w', encoding='utf-8') as f:
                json.dump(config_data, f, indent=2, ensure_ascii=False)
            return True
    
    async def load_config(self, default_config: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """
        从中央数据区加载插件配置数据。
        
        如果持久化的配置文件不存在，返回默认配置。
        
        Args:
            default_config: 默认配置，如果持久化配置不存在时返回
            
        Returns:
            配置数据字典
            
        Raises:
            PermissionError: 如果没有 CONFIG_DATA_READ 权限
        """
        # 检查读取权限
        read_permission_name = "config.data_read"
        if not self.permission_manager or not self.permission_manager.check_permission(self.get_metadata().name, read_permission_name):
            raise PermissionError("Plugin does not have permission to read configuration.")
        
        # 如果是在沙箱中运行，通过IPC发送加载请求
        if hasattr(self, '_send_ipc_request') and self._send_ipc_request is not None:
            return await self._send_ipc_request("load_config", default_config)
        else:
            # 非沙箱环境的fallback（主要用于测试）
            import json
            from pathlib import Path
            plugin_name = self.get_metadata().name
            data_file = Path("data") / f"{plugin_name}.json"
            
            if data_file.exists():
                with open(data_file, 'r', encoding='utf-8') as f:
                    return json.load(f)
            else:
                return default_config or {} 
    
    def get_config_schema(self) -> Optional[List[Dict[str, Any]]]:
        """
        返回插件的配置架构定义。
        
        插件可以覆盖此方法来定义其配置项的结构，用于生成通用配置页面。
        如果插件不提供自定义配置页面，但希望用户能通过通用界面配置，
        则必须实现此方法。
        
        Returns:
            配置架构列表，每个元素包含以下字段：
            - name (str): 配置项名称
            - label (str): 显示标签
            - type (str): 输入类型 ('text', 'password', 'number', 'textarea', 'select', 'checkbox')
            - required (bool): 是否必填，默认 False
            - default (Any): 默认值
            - placeholder (str): 输入提示文本，可选
            - help (str): 帮助文本，可选
            - options (List[Dict]): 对于 select 类型，选项列表 [{"value": "val", "label": "显示文本"}]
            
        Example:
            [
                {
                    "name": "api_key",
                    "label": "API 密钥",
                    "type": "password",
                    "required": True,
                    "placeholder": "请输入你的 API 密钥",
                    "help": "从服务商获取的 API 密钥"
                },
                {
                    "name": "timeout",
                    "label": "超时时间(秒)",
                    "type": "number",
                    "default": 30,
                    "help": "请求超时时间，建议 10-60 秒"
                }
            ]
        """
        return None


class MetaPlugin(PluginInterface, ABC):
    """
    元插件基类，用于管理插件组。
    元插件可以包含多个子插件，并管理它们的生命周期。
    """
    
    def __init__(self, http_client: httpx.AsyncClient, permission_manager: Optional[SandboxPermissionManager] = None):
        super().__init__(http_client, permission_manager)
        self.subplugins: Dict[str, PluginInterface] = {}
        self.chains: Dict[str, Dict[str, Any]] = {}
        self.group_config: Dict[str, Any] = {}
        self.is_meta_plugin = True
        self.subplugin_loader = None  # 延迟初始化
        
    async def load_subplugins(self) -> None:
        """加载所有子插件"""
        # 由具体实现类实现
        pass
        
    async def register_chains(self) -> None:
        """注册预设调用链到主框架"""
        # 由具体实现类实现
        pass
        
    async def manage_subplugin(self, action: str, plugin_name: str) -> bool:
        """
        管理子插件（启用/禁用/重载）
        
        Args:
            action: 动作类型 ('enable', 'disable', 'reload')
            plugin_name: 子插件名称
            
        Returns:
            操作是否成功
        """
        if plugin_name not in self.subplugins:
            self.logger.error(f"Subplugin {plugin_name} not found")
            return False
            
        try:
            if action == 'enable':
                # 启用子插件
                await self.subplugins[plugin_name].initialize()
                self.logger.info(f"Subplugin {plugin_name} enabled")
                return True
                
            elif action == 'disable':
                # 禁用子插件
                await self.subplugins[plugin_name].shutdown()
                self.logger.info(f"Subplugin {plugin_name} disabled")
                return True
                
            elif action == 'reload':
                # 重载子插件
                await self.subplugins[plugin_name].shutdown()
                await self.subplugins[plugin_name].initialize()
                self.logger.info(f"Subplugin {plugin_name} reloaded")
                return True
                
            else:
                self.logger.error(f"Unknown action: {action}")
                return False
                
        except Exception as e:
            self.logger.error(f"Failed to {action} subplugin {plugin_name}: {e}")
            return False
    
    def get_subplugin(self, name: str) -> Optional[PluginInterface]:
        """获取子插件实例"""
        return self.subplugins.get(name)
    
    def list_subplugins(self) -> List[str]:
        """列出所有子插件"""
        return list(self.subplugins.keys())
    
    async def handle(self, context: RequestContext, next_plugin: Optional[Callable[[RequestContext], Awaitable[None]]] = None) -> None:
        """
        元插件的handle方法默认实现。
        元插件本身通常不处理请求，而是作为管理器。
        """
        # 元插件默认直接传递给下一个插件
        if next_plugin:
            await next_plugin(context)
        else:
            # 如果没有下一个插件，记录警告
            self.logger.warning("MetaPlugin reached end of chain without processing") 