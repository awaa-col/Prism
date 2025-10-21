"""
Plugin loader with sandbox support.
"""

import os
import sys
import importlib.util
import asyncio
import json
import re
from typing import Dict, List, Optional, Type, Set, Any, TYPE_CHECKING, cast, Callable
from pathlib import Path
import multiprocessing as mp
from concurrent.futures import ProcessPoolExecutor

import httpx
import yaml
from httpx import AsyncClient
from packaging.specifiers import SpecifierSet, InvalidSpecifier
from packaging.version import parse as parse_version, InvalidVersion

if TYPE_CHECKING:
    from fastapi import APIRouter
    from app.services.plugin_installer import PluginInstallerService

from app.core.config import get_settings
from app.core.structured_logging import get_logger
from app.core.audit_sandbox import AuditHookManager, current_plugin_context
from app.plugins.interface import PluginInterface, PluginMetadata, SandboxPermissionManager, SandboxPermission, MetaPlugin
from app.plugins.subplugin_loader import SubPluginLoader, ChainRegistry
from app.plugins.signature import PluginSignatureVerifier
from app.utils.plugin_registry import PluginRegistry
from app.core.permission_engine import get_permission_engine

class SecurityError(Exception):
    """安全相关错误"""
    pass


class PluginNotFoundException(Exception):
    """插件未找到时抛出。"""
    pass


class PrivilegedPluginContext:
    """特权插件上下文，携带系统级能力"""
    def __init__(self):
        self.installer: Optional["PluginInstallerService"] = None
        self.register_root_router: Optional[Callable[["APIRouter", str], None]] = None

settings = get_settings()
logger = get_logger("plugin.loader")


class DependencyResolver:
    """插件依赖解析器"""
    
    def __init__(self):
        self.dependency_graph: Dict[str, Set[str]] = {}
        self.resolved_order: List[str] = []
    
    def add_plugin(self, plugin_name: str, dependencies: List[str]):
        """添加插件及其依赖关系"""
        self.dependency_graph[plugin_name] = set(dependencies)
    
    def resolve_dependencies(self) -> List[str]:
        """解析依赖关系，返回加载顺序"""
        resolved = set()
        temp_mark = set()
        
        def visit(node: str):
            if node in temp_mark:
                raise ValueError(f"Circular dependency detected involving {node}")
            if node in resolved:
                return
            
            temp_mark.add(node)
            
            for dependency in self.dependency_graph.get(node, set()):
                if dependency not in self.dependency_graph:
                    logger.warning(f"Missing dependency: {dependency} required by {node}")
                    continue
                visit(dependency)
            
            temp_mark.remove(node)
            resolved.add(node)
            self.resolved_order.append(node)
        
        for plugin in self.dependency_graph:
            if plugin not in resolved:
                visit(plugin)
        
        return self.resolved_order


class SecurityValidator:
    """插件安全验证器"""

    def validate_permissions(self, metadata: PluginMetadata) -> List[str]:
        """验证插件权限，返回警告列表"""
        warnings = []
        engine = get_permission_engine()
        
        for permission in metadata.permissions:
            perm_type = permission.type
            # 兼容两段式声明（type+resource），若能映射到内核定义则不警告
            try:
                mapped = engine.find_definition_for_declaration(str(perm_type or ""), permission.resource or "")
            except Exception:
                mapped = None
            if mapped is None and (not isinstance(perm_type, str) or 
                                   not re.match(r"^[a-z0-9_]+\.[a-z0-9_.]+$", perm_type)):
                warnings.append(
                    f"Plugin {metadata.name} uses an invalid permission format: '{perm_type}'. Expected 'namespace.action' or (type + resource)."
                )
        
        for permission in metadata.permissions:
            perm_type = permission.type
            if perm_type.startswith("system."):
                warnings.append(
                    f"Plugin {metadata.name} requests potentially dangerous system permission: {perm_type}"
                )
        
        return warnings


class PluginLoader:
    """Async plugin loader with optional sandbox support"""
    
    def __init__(self, plugin_dir: str = None, app=None):
        self.plugin_dir = Path(plugin_dir or settings.plugins.directory)
        self.plugins: Dict[str, PluginInterface] = {}
        self.plugin_metadata: Dict[str, PluginMetadata] = {}
        self.http_client: Optional[httpx.AsyncClient] = None
        self.app = app
        # Direct plugin loading (sandbox removed for security reasons)
        self.dependency_resolver = DependencyResolver()
        self.security_validator = SecurityValidator()
        self.permission_manager = SandboxPermissionManager()
        # 初始化并激活审计钩子管理器
        self.audit_manager = AuditHookManager(self.permission_manager)
        self.audit_manager.activate()
        # 可选的签名校验器
        self._signature_verifier = None
        if getattr(settings.plugins, 'verify_signatures', False):
            self._signature_verifier = PluginSignatureVerifier(
                trusted_keys_dir=getattr(settings.plugins, 'trusted_keys_dir', 'trusted_keys')
            )
        # 依赖声明与版本约束缓存
        self.declared_dependencies: Dict[str, List[str]] = {}
        self.version_constraints: Dict[str, Dict[str, str]] = {}
        # 插件根目录映射：用于沙箱路径检查
        self.plugin_root_paths: Dict[str, str] = {}
        # 插件注册表：仅允许“已安装”的插件被加载
        self.registry = PluginRegistry()

    def _create_privileged_context(self, plugin_name: str, metadata: PluginMetadata) -> Optional[PrivilegedPluginContext]:
        """创建特权插件上下文（基于声明的 system.* 能力，而非白名单）"""
        context = PrivilegedPluginContext()
        # 根据权限注入能力
        for permission in (metadata.permissions or []):
            if permission.type == "system.plugins.install":
                from app.services.plugin_installer import PluginInstallerService
                context.installer = PluginInstallerService()
            elif permission.type == "system.routes.create_root":
                context.register_root_router = self._create_safe_router_registrar(permission)
        return context

    def _create_safe_router_registrar(self, permission):
        """创建安全的根路由注册器"""
        if not self.app:
            return None
        
        RESERVED_PREFIXES = ("/api", "/admin", "/docs", "/redoc", "/metrics", "/static")
        allowed_prefix = getattr(permission, 'resource', None)
        
        def safe_registrar(router, prefix: str):
            if prefix != allowed_prefix:
                raise PermissionError(f"Plugin not authorized to register root prefix '{prefix}'")
            
            if any(prefix.startswith(reserved) for reserved in RESERVED_PREFIXES):
                raise SecurityError(f"Prefix '{prefix}' conflicts with reserved system route")
            
            self.app.include_router(router, prefix=prefix)
            logger.info("Registered root router", prefix=prefix)
        
        return safe_registrar

    async def initialize(self) -> None:
        """Initialize the plugin loader"""
        # Create shared HTTP client with connection pooling
        self.http_client = httpx.AsyncClient(
            timeout=httpx.Timeout(30.0),
            limits=httpx.Limits(max_keepalive_connections=50, max_connections=100),
            # http2 disabled to avoid optional dependency requirement (h2)
        )
        
        # Plugin loader initialized, sandboxing is handled by the audit hook manager
        logger.info("Plugin loader initialized; sandboxing is enforced via audit hooks.")
    
    async def shutdown(self) -> None:
        """Shutdown all plugins and cleanup resources"""
        # Shutdown all plugins
        shutdown_tasks = []
        for plugin_name, plugin in self.plugins.items():
            logger.info("Shutting down plugin", plugin=plugin_name)
            shutdown_tasks.append(plugin.shutdown())
        
        if shutdown_tasks:
            await asyncio.gather(*shutdown_tasks, return_exceptions=True)
        
        # Close HTTP client
        if self.http_client:
            await self.http_client.aclose()
        
        # Direct plugin loading shutdown
        logger.info("Direct plugin loading shutdown")
        
        self.plugins.clear()
        self.plugin_metadata.clear()

    async def _load_plugin_directly(self, plugin_path: Path, plugin_name: str, 
                                   config: Dict[str, Any], http_client: Optional[AsyncClient] = None) -> Optional[PluginInterface]:
        """直接加载插件（无沙箱）"""
        try:
            # 动态导入插件模块
            spec = importlib.util.spec_from_file_location(
                f"plugin_{plugin_name}", 
                plugin_path
            )
            if not spec or not spec.loader:
                logger.error("Failed to create module spec", plugin=plugin_name, file=str(plugin_path))
                return None
            
            module = importlib.util.module_from_spec(spec)
            sys.modules[spec.name] = module
            spec.loader.exec_module(module)
            
            # 查找插件类
            plugin_class = None
            for name in dir(module):
                obj = getattr(module, name)
                if (isinstance(obj, type) and 
                    issubclass(obj, PluginInterface) and 
                    obj != PluginInterface):
                    plugin_class = obj
                    break
            
            if not plugin_class:
                logger.error("No plugin class found in module", plugin=plugin_name, file=str(plugin_path))
                return None
            
            # 创建插件实例 - 所有 PluginInterface 子类都使用相同的构造参数
            client = http_client or self.http_client
            if not client:
                raise RuntimeError("HTTP client has not been initialized.")

            plugin_instance = plugin_class(
                http_client=client,
                permission_manager=self.permission_manager
            )
            
            return plugin_instance
            
        except Exception as e:
            logger.error("Failed to load plugin directly", plugin=plugin_name, file=str(plugin_path), error=str(e), exc_info=True)
            return None
    
    async def _load_plugin_metadata(self, plugin_name: str) -> Optional[PluginMetadata]:
        """加载插件元数据（不实例化插件）"""
        try:
            plugin_path = self.plugin_dir / plugin_name
            
            if not plugin_path.exists() or not plugin_path.is_dir():
                logger.error("Plugin directory not found", plugin=plugin_name, path=str(plugin_path))
                return None
            
            # Look for plugin.py or __init__.py
            module_file = None
            for filename in ["plugin.py", "__init__.py"]:
                file_path = plugin_path / filename
                if file_path.exists():
                    module_file = file_path
                    break
            
            if not module_file:
                logger.error("Plugin module not found", plugin=plugin_name)
                return None
 
            # 优先从 manifest 读取依赖与版本（无需导入第三方代码）
            manifest_version = "unknown"
            manifest_deps: List[str] = []
            manifest_permissions: List[Dict[str, Any]] = []
            manifest_file_yaml = plugin_path / "plugin.yml"
            manifest_file_json = plugin_path / "plugin.json"
            try:
                if manifest_file_yaml.exists():
                    data = yaml.safe_load(manifest_file_yaml.read_text(encoding="utf-8")) or {}
                    manifest_version = str(data.get("version", manifest_version))
                    raw_deps = data.get("dependencies", []) or []
                    manifest_deps = [str(x) for x in raw_deps]
                    manifest_permissions = data.get("permissions", []) or []
                elif manifest_file_json.exists():
                    data = json.loads(manifest_file_json.read_text(encoding="utf-8")) or {}
                    manifest_version = str(data.get("version", manifest_version))
                    raw_deps = data.get("dependencies", []) or []
                    manifest_deps = [str(x) for x in raw_deps]
                    manifest_permissions = data.get("permissions", []) or []
            except (yaml.YAMLError, json.JSONDecodeError) as e:
                logger.warning("Failed to parse plugin manifest", plugin=plugin_name, error=str(e))
            except Exception as e:
                logger.warning("Failed to read plugin manifest", plugin=plugin_name, error=str(e))

            # 解析依赖中的版本约束（name@constraint），resolver 仅使用名字栈构图
            dep_names: List[str] = []
            constraints: Dict[str, str] = {}
            for spec in manifest_deps:
                name, constraint = self._parse_dependency_spec(spec)
                dep_names.append(name)
                if constraint:
                    constraints[name] = constraint
            self.declared_dependencies[plugin_name] = dep_names
            self.version_constraints[plugin_name] = constraints

            # 解析权限配置
            parsed_permissions = []
            logger.info("Parsing permissions for plugin", plugin=plugin_name, permissions_count=len(manifest_permissions), permissions=manifest_permissions)
            for perm_dict in manifest_permissions:
                perm_type = perm_dict.get("type")
                if isinstance(perm_type, str):
                    parsed_permissions.append(SandboxPermission(
                        type=perm_type,
                        resource=perm_dict.get("resource"),
                        description=perm_dict.get("description", "")
                    ))
                    logger.info("Successfully parsed permission", plugin=plugin_name, permission_type=perm_type)
                else:
                    logger.warning("Invalid permission in manifest", plugin=plugin_name, permission=perm_dict)

            # 基元数据用于依赖解析阶段
            basic_metadata = PluginMetadata(
                name=plugin_name,
                version=manifest_version,
                description=f"Plugin {plugin_name}",
                author="unknown",
                dependencies=dep_names,
                permissions=parsed_permissions,
                sandbox_required=True
            )
            logger.info("Basic metadata created for plugin", plugin=plugin_name)
            return basic_metadata
             
        except Exception as e:
            logger.error("Failed to load plugin metadata", plugin=plugin_name, error=str(e), exc_info=True)
            return None

    def _parse_dependency_spec(self, spec: str) -> tuple[str, Optional[str]]:
        """解析依赖规范 'name@constraint'，返回 (name, constraint)。"""
        spec = (spec or "").strip()
        if not spec:
            return "", None
        if "@" in spec:
            name, constraint = spec.split("@", 1)
            return name.strip(), constraint.strip()
        return spec, None

    def _version_satisfies(self, version: str, constraint: str) -> bool:
        """使用 packaging 库检查版本是否满足约束。"""
        if not constraint:
            return True
        try:
            # packaging.version.parse can handle "v1.0.0" and "1.0.0"
            parsed_v = parse_version(version)
            # SpecifierSet handles complex rules like ">=1.0, !=1.5, <2.0"
            spec = SpecifierSet(constraint)
            return parsed_v in spec
        except (InvalidVersion, InvalidSpecifier) as e:
            logger.warning(
                "Could not validate version constraint",
                version=version,
                constraint=constraint,
                error=str(e)
            )
            # 无法解析时，采取宽容策略，避免因格式问题导致插件加载失败
            return True
        except Exception as e:
            logger.error(
                "An unexpected error occurred during version satisfaction check",
                version=version,
                constraint=constraint,
                error=str(e)
            )
            return False
    
    async def _verify_signature_if_needed(self, plugin_name: str) -> bool:
        if not self._signature_verifier:
            return True
        try:
            plugin_path = self.plugin_dir / plugin_name
            verified, err = self._signature_verifier.verify_plugin(plugin_path)
            if not verified:
                logger.error("Plugin signature verification failed", plugin=plugin_name, error=err)
                return False
            logger.info("Plugin signature verified", plugin=plugin_name)
            return True
        except Exception as e:
            logger.error("Plugin signature verification error", plugin=plugin_name, error=str(e))
            return False

    async def load_plugin(self, plugin_name: str, allowed_domains: List[str] = None) -> Optional[PluginInterface]:
        """Load a single plugin"""
        try:
            plugin_path = self.plugin_dir / plugin_name
            
            if not plugin_path.exists() or not plugin_path.is_dir():
                logger.error("Plugin directory not found", plugin=plugin_name, path=str(plugin_path))
                return None
            
            # 仅允许“已安装”的插件被加载（必须存在注册表记录）
            try:
                registry_info = await self.registry.get_plugin_info(plugin_name)
            except Exception:
                registry_info = None
            if not registry_info:
                logger.warning("Skip loading plugin not marked as installed in registry", plugin=plugin_name)
                return None
            
            # 签名校验（可选）
            if not await self._verify_signature_if_needed(plugin_name):
                return None
            
            # 检查是否是插件组
            if (plugin_path / "group.yml").exists():
                return await self.load_plugin_group(plugin_name, allowed_domains)
            
            # Look for plugin.py or __init__.py
            module_file = None
            for filename in ["plugin.py", "__init__.py"]:
                file_path = plugin_path / filename
                if file_path.exists():
                    module_file = file_path
                    break
            
            if not module_file:
                logger.error("Plugin module not found", plugin=plugin_name)
                return None
            
            # 首先加载基础元数据（包含从 plugin.json 解析的权限）
            basic_metadata = await self._load_plugin_metadata(plugin_name)
            if not basic_metadata:
                logger.error("Failed to load basic metadata", plugin=plugin_name)
                return None
            
            # 直接加载插件（沙箱已移除）
            config = {}  # 普通插件配置，可以从配置文件读取
            plugin_instance = await self._load_plugin_directly(module_file, plugin_name, config, self.http_client)
            if not plugin_instance:
                logger.error("Failed to load plugin directly", plugin=plugin_name)
                return None
            
            # 使用基础元数据中的权限配置，而不是插件实例的元数据
            metadata = basic_metadata
            
            # 预先注册插件根目录路径，确保审计在初始化阶段可识别
            self.plugin_root_paths[metadata.name] = str(plugin_path.absolute())
            # 立即注入到审计管理器，避免初始化阶段写文件时报未注册错误
            self.audit_manager.set_plugin_root_paths(self.plugin_root_paths)
            
            # 安全验证（仍保留告警能力）
            security_warnings = self.security_validator.validate_permissions(metadata)
            for warning in security_warnings:
                logger.warning(warning)
            
            # 仅从安装流程生成的锁文件读取权限；无锁则拒绝加载
            lock_file = plugin_path / "permissions.lock.json"
            if not lock_file.exists():
                logger.warning("Permission lock file missing; plugin is not considered installed. Skipping load.", plugin=metadata.name)
                return None
            self._load_permissions_from_lock_file(metadata.name)
            
            # 在授予权限后再初始化插件，确保初始化阶段的文件读写不被拦截
            logger.info("Initializing plugin within sandbox context", plugin=metadata.name)
            with current_plugin_context.use(metadata.name):
                await plugin_instance.initialize()
            
            # 插件根目录路径已在初始化前注入审计管理器
            # Store plugin and metadata
            self.plugins[metadata.name] = plugin_instance
            self.plugin_metadata[metadata.name] = metadata
            
            # 如果插件具有配置读取权限，尝试加载持久化配置
            await self._load_plugin_persistent_config(plugin_instance, metadata)
            
            logger.info(
                "Plugin loaded successfully",
                plugin=metadata.name,
                version=metadata.version,
                sandboxed=True,
                permissions_count=len(self.permission_manager.granted_permissions.get(metadata.name, set()))
            )
            
            return plugin_instance
            
        except Exception as e:
            logger.error("Failed to load plugin", plugin=plugin_name, error=str(e), exc_info=True)
            return None
    
    async def load_plugin_group(self, plugin_name: str, allowed_domains: List[str] = None) -> Optional[MetaPlugin]:
        """加载插件组"""
        try:
            plugin_path = self.plugin_dir / plugin_name
            
            # 首先作为普通插件加载元插件
            module_file = None
            for filename in ["plugin.py", "__init__.py"]:
                file_path = plugin_path / filename
                if file_path.exists():
                    module_file = file_path
                    break
            
            if not module_file:
                logger.error("Meta plugin module not found", plugin=plugin_name)
                return None
            
            # 直接加载元插件（沙箱已移除）
            config = {}  # 元插件配置，可以从配置文件读取
            meta_plugin_instance = await self._load_plugin_directly(module_file, plugin_name, config)
            if not meta_plugin_instance:
                logger.error("Failed to load meta plugin directly", plugin=plugin_name)
                return None
            
            # 设置插件目录
            meta_plugin_instance.plugin_dir = plugin_path
            
            # 初始化元插件（初始化后可进行能力探测）
            await meta_plugin_instance.initialize()
            
            # 验证是否是MetaPlugin（兼容沙箱代理：检查能力标志）
            if not meta_plugin_instance or not (isinstance(meta_plugin_instance, MetaPlugin) or getattr(meta_plugin_instance, 'is_meta_plugin', False)):
                logger.error("Plugin is not a MetaPlugin", plugin=plugin_name)
                return None
            
            meta_plugin_instance = cast(MetaPlugin, meta_plugin_instance)
            
            # 首先加载基础元数据（包含从 plugin.json 解析的权限）
            basic_metadata = await self._load_plugin_metadata(plugin_name)
            if not basic_metadata:
                logger.error("Failed to load basic metadata for plugin group", plugin=plugin_name)
                return None
            
            # 使用基础元数据中的权限配置
            metadata = basic_metadata
            
            # 生成权限锁文件并设置权限
            permissions = metadata.permissions or []
            self._create_permission_lock_file(metadata.name, permissions)
            self._load_permissions_from_lock_file(metadata.name)
            
            # 记录插件根目录绝对路径
            self.plugin_root_paths[metadata.name] = str(plugin_path.absolute())
            
            # 创建子插件加载器
            subplugin_loader = SubPluginLoader(meta_plugin_instance)
            meta_plugin_instance.subplugin_loader = subplugin_loader
            
            # 加载所有子插件
            await subplugin_loader.load_all()
            
            # 解析依赖关系
            await subplugin_loader.resolve_dependencies()
            
            # 创建调用链注册器
            chain_registry = ChainRegistry(meta_plugin_instance)
            
            # 注册预设调用链
            await chain_registry.register_preset_chains()
            
            # 存储元插件
            self.plugins[metadata.name] = meta_plugin_instance
            self.plugin_metadata[metadata.name] = metadata
            
            # 加载持久化配置
            await self._load_plugin_persistent_config(meta_plugin_instance, metadata)
            
            logger.info(
                "Plugin group loaded successfully",
                plugin=metadata.name,
                version=metadata.version,
                subplugins_count=len(meta_plugin_instance.subplugins),
                chains_count=len(meta_plugin_instance.chains)
            )
            
            return meta_plugin_instance
            
        except Exception as e:
            logger.error("Failed to load plugin group", plugin=plugin_name, error=str(e), exc_info=True)
            return None
    
    def _get_allowed_domains_for_plugin(self, metadata: PluginMetadata) -> List[str]:
        """获取插件允许访问的域名列表"""
        allowed_domains = []
        
        for permission in metadata.permissions:
            if permission.type == "network.specific_domain" and permission.resource:
                allowed_domains.append(permission.resource)
        
        # 如果没有指定域名权限，检查是否有通用网络权限
        has_general_network = any(
            p.type in ("network.http", "network.https")
            for p in metadata.permissions
        )
        
        if has_general_network and not allowed_domains:
            # 默认允许一些常见的API域名
            allowed_domains = [
                "api.openai.com",
                "api.anthropic.com",
                "api.cohere.ai",
                "generativelanguage.googleapis.com"
            ]
        
        return allowed_domains
    
    async def load_all_plugins(self) -> Dict[str, PluginInterface]:
        """Load all plugins from the plugin directory with dependency resolution"""
        if not self.plugin_dir.exists():
            logger.warning("Plugin directory does not exist", path=str(self.plugin_dir))
            return {}
        
        # Get list of potential plugin directories
        plugin_dirs = [
            d for d in self.plugin_dir.iterdir()
            if d.is_dir() and not d.name.startswith("_")
        ]
        # 仅允许“已安装（registry 已登记）”的插件进入候选
        try:
            installed_map = await self.registry.list_plugins()
            installed_names = set(installed_map.keys())
        except Exception:
            installed_names = set()
        plugin_dirs = [d for d in plugin_dirs if d.name in installed_names]

        # 进一步按 enabled 白名单过滤（如果提供）
        if settings.plugins.enabled:
            plugin_dirs = [d for d in plugin_dirs if d.name in settings.plugins.enabled]
        elif not settings.plugins.auto_load:
            # 当没有 enabled 且关闭 auto_load 时，不加载任何插件
            if not plugin_dirs:
                logger.info("No plugins enabled and auto_load is false")
            return {}
        
        # Load metadata for dependency resolution
        logger.info("Loading plugin metadata for dependency resolution")
        metadata_tasks = []
        for plugin_dir in plugin_dirs:
            metadata_tasks.append(self._load_plugin_metadata(plugin_dir.name))
        
        if metadata_tasks:
            metadata_results = await asyncio.gather(*metadata_tasks, return_exceptions=True)
            
            # Build dependency graph
            for plugin_dir, metadata in zip(plugin_dirs, metadata_results):
                if isinstance(metadata, Exception):
                    logger.error(f"Failed to load metadata for {plugin_dir.name}", error=metadata)
                    continue
                if metadata:
                    self.dependency_resolver.add_plugin(metadata.name, metadata.dependencies or [])
            
            # Resolve dependencies
            try:
                load_order = self.dependency_resolver.resolve_dependencies()
                logger.info(f"Plugin load order determined: {load_order}")
            except ValueError as e:
                logger.error(f"Dependency resolution failed: {e}")
                # 硬失败：存在循环依赖，停止加载
                return {}
        else:
            load_order = [d.name for d in plugin_dirs]
        
        # Load plugins in dependency order
        for plugin_name in load_order:
            # 依赖是否已满足（存在与版本匹配）
            deps = self.declared_dependencies.get(plugin_name, [])
            constraints = self.version_constraints.get(plugin_name, {})
            unmet: List[str] = []
            for dep in deps:
                if dep not in self.plugin_metadata:
                    unmet.append(f"{dep}@{constraints.get(dep, '*')}")
                    continue
                dep_version = self.plugin_metadata[dep].version
                constraint = constraints.get(dep)
                if constraint and not self._version_satisfies(dep_version, constraint):
                    unmet.append(f"{dep}@{constraint} (have {dep_version})")
            if unmet:
                logger.error("Dependency not satisfied; skip loading", plugin=plugin_name, unmet=", ".join(unmet))
                continue

            plugin = await self.load_plugin(plugin_name)
            if isinstance(plugin, Exception) or plugin is None:
                logger.error(f"Plugin load failed: {plugin_name}: {plugin}")
        
        # 将插件根目录路径注入到审计管理器
        self.audit_manager.set_plugin_root_paths(self.plugin_root_paths)
        
        logger.info(f"Loaded {len(self.plugins)} plugins")
        return self.plugins
    
    def get_plugin(self, name: str) -> Optional[PluginInterface]:
        """Get a loaded plugin by name"""
        return self.plugins.get(name)
    
    def get_all_plugins(self) -> Dict[str, PluginInterface]:
        """Get all loaded plugins"""
        return self.plugins

    def list_plugins(self) -> List[Dict[str, Any]]:
        """
        List all plugins based on loaded metadata.
        This should be called after `load_all_plugins`.
        """
        plugin_list = []
        for name, metadata in self.plugin_metadata.items():
            plugin_list.append({
                "name": metadata.name,
                "description": metadata.description,
                "version": metadata.version,
                "author": metadata.author,
            })
        return plugin_list

    def get_plugin_metadata(self, name: str) -> Optional[PluginMetadata]:
        """Get plugin metadata by name"""
        return self.plugin_metadata.get(name)
    
    def get_permission_violations(self, plugin_name: str) -> List[str]:
        """Get permission violations for a plugin"""
        return self.permission_manager.get_violations(plugin_name)
    
    async def _load_plugin_persistent_config(self, plugin_instance, metadata: PluginMetadata) -> None:
        """
        为插件加载持久化配置数据。
        
        如果插件声明了CONFIG_DATA_READ权限，且存在持久化配置文件，
        则会尝试加载该配置并使其对插件可用。
        
        Args:
            plugin_instance: 插件实例
            metadata: 插件元数据
        """
        try:
            # 检查插件是否拥有配置读取权限（基于权限管理器）
            has_config_read_permission = self.permission_manager.has_permission_prefix(metadata.name, "config.")
            
            if not has_config_read_permission:
                logger.debug("Plugin has no config read permission, skipping config load", 
                           plugin=metadata.name)
                return
            
            # 检查持久化配置文件是否存在
            from pathlib import Path
            import json
            
            data_file = Path("data") / f"{metadata.name}.json"
            
            if data_file.exists():
                logger.info("Loading persistent config for plugin", 
                          plugin=metadata.name, 
                          config_file=str(data_file))
                
                # 由于插件在沙箱中运行，我们不能直接传递配置对象
                # 配置会在插件调用 load_config() 时被动态加载
                # 这里我们只是记录配置文件的存在
                logger.info("Persistent config file found", 
                          plugin=metadata.name, 
                          file_size=data_file.stat().st_size)
            else:
                logger.debug("No persistent config file found for plugin", plugin=metadata.name)
                
        except Exception as e:
            logger.warning("Failed to check plugin persistent config", 
                         plugin=metadata.name, 
                         error=str(e))
    
    def _create_permission_lock_file(self, plugin_name: str, permissions: List[SandboxPermission]) -> None:
        """
        创建权限锁文件，存储在插件自己的根目录中。
        这个文件是权限的唯一真相来源，插件无法修改。
        """
        try:
            plugin_path = self.plugin_dir / plugin_name
            lock_file = plugin_path / "permissions.lock.json"
            
            # 序列化权限数据
            permissions_data = {
                "plugin_name": plugin_name,
                "permissions": [
                    {
                        "type": perm.type,
                        "resource": perm.resource,
                        "description": perm.description
                    }
                    for perm in permissions
                ],
                "created_at": str(asyncio.get_event_loop().time()),
                "version": "1.0"
            }
            
            # 无论是否存在，都直接覆盖锁文件，以确保更新时权限能正确应用
            with open(lock_file, 'w', encoding='utf-8') as f:
                json.dump(permissions_data, f, indent=2, ensure_ascii=False)
            
            logger.info(f"Created/Updated permission lock file for plugin {plugin_name}", 
                       lock_file=str(lock_file), 
                       permissions_count=len(permissions))
                       
        except Exception as e:
            logger.error(f"Failed to create permission lock file for plugin {plugin_name}: {e}")
            raise
    
    def _load_permissions_from_lock_file(self, plugin_name: str) -> None:
        """
        从权限锁文件加载权限并设置到权限管理器。
        这是权限的唯一加载路径，确保安全性。
        """
        try:
            plugin_path = self.plugin_dir / plugin_name
            lock_file = plugin_path / "permissions.lock.json"
            
            if not lock_file.exists():
                logger.warning(f"Permission lock file not found for plugin {plugin_name}")
                return
            
            # 读取锁文件
            with open(lock_file, 'r', encoding='utf-8') as f:
                permissions_data = json.load(f)
            
            # 重建权限对象
            permissions = []
            for perm_data in permissions_data.get("permissions", []):
                perm_type = perm_data.get("type")
                if isinstance(perm_type, str):
                    permission = SandboxPermission(
                        type=perm_type,
                        resource=perm_data.get("resource"),
                        description=perm_data.get("description", "")
                    )
                    permissions.append(permission)
                else:
                    logger.warning(f"Invalid permission type in lock file: {perm_type}")
                    continue
            
            # 设置权限到管理器
            self.permission_manager.grant_permissions(plugin_name, permissions)
            
            logger.info(f"Loaded {len(permissions)} permissions from lock file for plugin {plugin_name}")
            
        except Exception as e:
            logger.error(f"Failed to load permissions from lock file for plugin {plugin_name}: {e}")
            raise

    async def reload_plugin(self, name: str) -> bool:
        """Reload a specific plugin"""
        # Shutdown existing plugin
        if name in self.plugins:
            await self.plugins[name].shutdown()
            del self.plugins[name]
            del self.plugin_metadata[name]
            # 清理权限和路径映射
            if name in self.plugin_root_paths:
                del self.plugin_root_paths[name]
        
        # Reload the plugin
        plugin = await self.load_plugin(name)
        return plugin is not None
    
    async def unload_plugin(self, name: str) -> bool:
        """Unload a specific plugin"""
        if name not in self.plugins:
            return False
        
        try:
            # Shutdown the plugin
            await self.plugins[name].shutdown()
            
            # Remove from loaded plugins
            del self.plugins[name]
            del self.plugin_metadata[name]
            
            # 清理权限和路径映射
            if name in self.plugin_root_paths:
                del self.plugin_root_paths[name]
            
            logger.info("Plugin unloaded successfully", plugin=name)
            return True
        except Exception as e:
            logger.error("Failed to unload plugin", plugin=name, error=str(e))
            return False
    
    def validate_plugin_security(self, plugin_name: str) -> Dict[str, any]:
        """Validate plugin security status"""
        metadata = self.plugin_metadata.get(plugin_name)
        if not metadata:
            return {"error": "Plugin not found"}
        
        return {
            "plugin": plugin_name,
            "sandbox_required": metadata.sandbox_required,
            "sandbox_enabled": settings.sandbox.enabled,
            "permissions": [
                {
                    "type": p.type if isinstance(p.type, str) else p.type.value,
                    "resource": p.resource,
                    "description": p.description
                }
                for p in metadata.permissions
            ],
            "violations": self.get_permission_violations(plugin_name),
            "security_warnings": self.security_validator.validate_permissions(metadata)
        }
    
    async def get_all_routers(self) -> Dict[str, 'APIRouter']:
        """
        Dynamically assembles API routers from plugin schemas, avoiding non-picklable objects in closures.
        This prevents a `deepcopy` error in FastAPI's dependency resolution when using sandboxed plugins.
        """
        from fastapi import APIRouter, Depends, HTTPException, Request, Response
        from app.api.deps import get_current_user, get_current_admin_user, get_api_key_or_user

        routers: Dict[str, APIRouter] = {}

        def create_handler(plugin_name: str, handler_name: str):
            """
            Creates a closure for the endpoint handler.
            The closure only captures strings (plugin_name, handler_name), which are picklable.
            The actual plugin instance is retrieved from request.app.state at request time.
            """
            async def endpoint(request: Request):
                try:
                    loader = request.app.state.plugin_loader
                    plugin = loader.get_plugin(plugin_name)
                    if not plugin:
                        logger.error("Plugin not found during request", plugin=plugin_name)
                        raise HTTPException(status_code=500, detail=f"Plugin '{plugin_name}' not found")

                    body = None
                    if request.method in ("POST", "PUT", "PATCH"):
                        try:
                            body = await request.json()
                        except Exception as json_error:
                            logger.error(
                                "Failed to parse request JSON body",
                                plugin=plugin_name,
                                handler=handler_name,
                                error=str(json_error),
                                exc_info=True
                            )
                            body = None
                    
                    payload = {
                        "path_params": dict(request.path_params),
                        "query": dict(request.query_params),
                        "body": body,
                        "headers": dict(request.headers),
                        "route": request.url.path, # 传递路由信息
                    }
                    
                    # 调用插件的 invoke 方法 (within sandbox context)
                    with current_plugin_context.use(plugin_name):
                        result = await plugin.invoke(handler_name, payload)
 
                    # 若插件直接返回 FastAPI/Starlette Response，则原样返回
                    if isinstance(result, Response):
                        return result
                    if isinstance(result, dict) and 'content' in result:
                        return Response(
                            content=result.get('content'),
                            media_type=result.get('media_type', 'application/octet-stream'),
                            status_code=result.get('status_code', 200),
                        )
                    
                    from app.utils.responses import APIResponse
                    return APIResponse.success(data=result)
                except Exception as e:
                    logger.error("Error in plugin endpoint", plugin=plugin_name, handler=handler_name, error=str(e), exc_info=True)
                    raise HTTPException(status_code=500, detail=f"Error in plugin '{plugin_name}': {e}")
            return endpoint

        for plugin_name, plugin in self.plugins.items():
            try:
                if not hasattr(plugin, 'get_route_schema'):
                    continue
                
                schema = plugin.get_route_schema()
                if not schema:
                    continue
                
                # 权限检查：插件必须有 api.create_route 权限才能注册路由
                metadata = self.get_plugin_metadata(plugin_name)
                if not metadata:
                    logger.error("Could not find metadata for plugin while creating router", plugin=plugin_name)
                    continue
                has_route_perm = self.permission_manager.check_permission(plugin_name, "api.create_route")

                if not has_route_perm:
                    logger.warning("Plugin attempted to create API routes without 'api.create_route' permission. Routes ignored.", plugin=plugin_name)
                    continue # 跳过此插件的路由

                router = APIRouter()
                
                for item in schema:
                    method = (item.get('method') or 'GET').upper()
                    path = item.get('path') or '/'
                    if not path.startswith('/'):
                        path = '/' + path
                    handler_name = (item.get('handler') or f"{method}:{path}")
                    auth = (item.get('auth') or 'none').lower()
                    
                    dependencies = []
                    if auth == 'user':
                        dependencies = [Depends(get_current_user)]
                    elif auth == 'admin':
                        dependencies = [Depends(get_current_admin_user)]
                    elif auth == 'api_key':
                        dependencies = [Depends(get_api_key_or_user)]
                    
                    handler_func = create_handler(plugin_name, handler_name)
                    
                    router.add_api_route(path, endpoint=handler_func, methods=[method], dependencies=dependencies)
                
                routers[plugin_name] = router
                logger.info("Assembled router from schema", plugin=plugin_name)
            except Exception as e:
                logger.error("Failed to assemble router from plugin schema", plugin=plugin_name, error=str(e))
        
        return routers 