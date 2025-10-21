"""
子插件加载器模块
"""

import os
import sys
import importlib.util
from pathlib import Path
from typing import Dict, Any, Optional, List
import yaml

from app.core.structured_logging import get_logger
from app.plugins.interface import PluginInterface, MetaPlugin



class SubPluginLoader:
    """子插件加载器"""
    
    def __init__(self, meta_plugin: MetaPlugin):
        self.meta_plugin = meta_plugin
        self.plugin_dir = Path(meta_plugin.plugin_dir) if meta_plugin.plugin_dir else Path(".")
        self.subplugins_dir = self.plugin_dir / "subplugins"
        self.logger = get_logger(f"subplugin_loader.{meta_plugin.get_metadata().name}")
        self.group_config: Dict[str, Any] = {}

    def _normalize_group_config(self, cfg: Any) -> Dict[str, Any]:
        """将 group.yml 归一化为 {subplugins: dict, dependencies: dict[str,list[str]]}，防御空值与错误类型。"""
        if not isinstance(cfg, dict):
            return {"subplugins": {}, "dependencies": {}}
        sub = cfg.get("subplugins") or {}
        deps = cfg.get("dependencies") or {}
        if not isinstance(sub, dict):
            sub = {}
        if not isinstance(deps, dict):
            deps = {}
        norm_deps: Dict[str, List[str]] = {}
        for k, v in deps.items():
            if isinstance(v, list):
                norm_deps[k] = [str(x) for x in v if x is not None]
            elif isinstance(v, str):
                norm_deps[k] = [v]
            else:
                norm_deps[k] = []
        cfg["subplugins"] = sub
        cfg["dependencies"] = norm_deps
        return cfg
        
    async def load_group_config(self) -> Dict[str, Any]:
        """加载插件组配置"""
        group_config_path = self.plugin_dir / "group.yml"
        if not group_config_path.exists():
            self.logger.warning(f"Group config not found: {group_config_path}")
            return {}
            
        try:
            with open(group_config_path, 'r', encoding='utf-8') as f:
                raw = yaml.safe_load(f) or {}
                config = self._normalize_group_config(raw)
                self.group_config = config
                self.meta_plugin.group_config = config
                return config
        except Exception as e:
            self.logger.error(f"Failed to load group config: {e}")
            return {}
    
    async def load_all(self) -> None:
        """加载所有子插件"""
        if not self.subplugins_dir.exists():
            self.logger.warning(f"Subplugins directory not found: {self.subplugins_dir}")
            return
            
        # 先加载组配置
        await self.load_group_config()
        
        # 获取子插件配置
        subplugins_config = self.group_config.get('subplugins', {})
        # 构建依赖图（仅名字）
        deps_map: Dict[str, List[str]] = {}
        raw_deps = (self.group_config.get('dependencies', {}) or {})
        for name, deps in raw_deps.items():
            dep_names: List[str] = []
            for spec in deps or []:
                dname, _ = self._parse_dependency_spec(str(spec))
                if dname:
                    dep_names.append(dname)
            deps_map[name] = dep_names

        # 收集启用的子插件集合
        enabled_set = set()
        if self.subplugins_dir.exists():
            for subplugin_dir in self.subplugins_dir.iterdir():
                if subplugin_dir.is_dir() and not subplugin_dir.name.startswith('.'):
                    config = subplugins_config.get(subplugin_dir.name, {})
                    if config.get('enabled', True):
                        enabled_set.add(subplugin_dir.name)

        # 拓扑排序（只考虑启用的子插件）
        load_order: List[str] = []
        temp_mark = set()
        perm_mark = set()

        def visit(node: str):
            if node in perm_mark:
                return
            if node in temp_mark:
                raise ValueError(f"Circular subplugin dependency detected: {node}")
            temp_mark.add(node)
            for d in deps_map.get(node, []):
                if d in enabled_set:
                    visit(d)
            temp_mark.remove(node)
            perm_mark.add(node)
            load_order.append(node)

        try:
            for n in sorted(enabled_set):
                if n not in perm_mark:
                    visit(n)
        except ValueError as e:
            self.logger.error(str(e))
            return

        # 按顺序加载；缺失依赖直接跳过并记录
        for name in load_order:
            # 检查依赖是否均在已加载集合
            unmet = [d for d in deps_map.get(name, []) if d in enabled_set and d not in self.meta_plugin.subplugins]
            if unmet:
                self.logger.error(f"Skip loading {name}, unmet dependencies: {', '.join(unmet)}")
                continue
            await self.load_subplugin(name)
    
    async def load_subplugin(self, name: str) -> Optional[PluginInterface]:
        """加载单个子插件"""
        try:
            subplugin_path = self.subplugins_dir / name
            
            # 验证子插件结构
            if not self._validate_subplugin_structure(subplugin_path):
                self.logger.error(f"Invalid subplugin structure: {name}")
                return None
            
            # 加载子插件配置
            config = self._load_subplugin_config(subplugin_path)
            
            # 获取权限配置
            permissions = self._get_subplugin_permissions(name)
            
            # 使用元插件的HTTP客户端和权限管理器
            plugin_file = subplugin_path / "plugin.py"
            if not plugin_file.exists():
                self.logger.error(f"plugin.py not found for subplugin: {name}")
                return None
            
            # 直接加载插件（沙箱已移除）
            plugin_instance = await self._load_subplugin_directly(
                plugin_file, 
                name, 
                config, 
                self.meta_plugin.http_client
            )
            
            if plugin_instance:
                # 初始化插件
                await plugin_instance.initialize()
                
                # 获取元数据并设置权限
                metadata = plugin_instance.get_metadata()
                
                # 为子插件设置完整名称
                full_name = f"{self.meta_plugin.get_metadata().name}.{name}"
                
                # 使用元插件的权限管理器授予权限
                if self.meta_plugin.permission_manager and metadata.permissions:
                    self.meta_plugin.permission_manager.grant_permissions(full_name, metadata.permissions)
                
                # 注册到元插件
                self.meta_plugin.subplugins[name] = plugin_instance
                self.logger.info(f"Loaded subplugin: {name}")
                return plugin_instance
            else:
                self.logger.error(f"Failed to load subplugin: {name}")
                return None
                
        except Exception as e:
            self.logger.error(f"Error loading subplugin {name}: {e}")
            return None

    async def _load_subplugin_directly(self, plugin_path: Path, plugin_name: str, 
                                     config: Dict[str, Any], http_client) -> Optional[PluginInterface]:
        """直接加载子插件（无沙箱）"""
        try:
            spec = importlib.util.spec_from_file_location(f"subplugin_{plugin_name}", plugin_path)
            if not spec or not spec.loader:
                self.logger.error(f"Failed to create spec for subplugin: {plugin_name}")
                return None
                
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            
            plugin_class = getattr(module, 'Plugin', None)
            if not plugin_class:
                self.logger.error(f"Plugin class not found in subplugin: {plugin_name}")
                return None
                
            return plugin_class(config=config, http_client=http_client)
            
        except Exception as e:
            self.logger.error(f"Error loading subplugin directly '{plugin_name}': {e}", exc_info=True)
            return None
    
    def _validate_subplugin_structure(self, plugin_path: Path) -> bool:
        """验证子插件结构"""
        # 检查必需文件
        plugin_file = plugin_path / "plugin.py"
        if not plugin_file.exists():
            return False
            
        # 检查是否有插件类定义
        # 仅校验文件存在性，具体实现类由沙箱/加载流程基于反射再判定
        try:
            return plugin_file.exists()
        except Exception:
            return False
    
    def _load_subplugin_config(self, plugin_path: Path) -> Dict[str, Any]:
        """加载子插件配置"""
        config_file = plugin_path / "config.yml"
        if not config_file.exists():
            return {}
            
        try:
            with open(config_file, 'r', encoding='utf-8') as f:
                return yaml.safe_load(f) or {}
        except Exception as e:
            self.logger.error(f"Failed to load subplugin config: {e}")
            return {}
    
    def _get_subplugin_permissions(self, name: str) -> List[str]:
        """获取子插件权限"""
        # 从组配置中获取权限
        subplugins_config = self.group_config.get('subplugins', {})
        plugin_config = subplugins_config.get(name, {})
        return plugin_config.get('permissions', [])
    
    async def resolve_dependencies(self) -> None:
        """解析子插件依赖关系"""
        dependencies = self.group_config.get('dependencies', {})
        # 运行期检查，未满足仅告警（加载顺序在 load_all 中处理）
        for plugin_name, deps in (dependencies or {}).items():
            for spec in deps or []:
                dep, _ = self._parse_dependency_spec(str(spec))
                if dep and dep not in self.meta_plugin.subplugins:
                    self.logger.warning(
                        f"Subplugin {plugin_name} depends on {dep}, but {dep} is not loaded")

    def _parse_dependency_spec(self, spec: str) -> (str, Optional[str]):
        spec = (spec or "").strip()
        if not spec:
            return "", None
        if "@" in spec:
            n, c = spec.split("@", 1)
            return n.strip(), c.strip()
        return spec, None


class ChainRegistry:
    """调用链注册器"""
    
    def __init__(self, meta_plugin: MetaPlugin):
        self.meta_plugin = meta_plugin
        self.logger = get_logger(f"chain_registry.{meta_plugin.get_metadata().name}")
        
    async def load_chains_config(self) -> Dict[str, Any]:
        """加载调用链配置"""
        chains_path = Path(self.meta_plugin.plugin_dir) / "chains.yml"
        if not chains_path.exists():
            self.logger.warning(f"Chains config not found: {chains_path}")
            return {}
            
        try:
            with open(chains_path, 'r', encoding='utf-8') as f:
                return yaml.safe_load(f) or {}
        except Exception as e:
            self.logger.error(f"Failed to load chains config: {e}")
            return {}
    
    async def register_preset_chains(self) -> None:
        """注册预设调用链（兼容老格式列表写法），任何错误仅记录并跳过。"""
        chains_config = await self.load_chains_config()
        raw = chains_config.get('chains', {})

        # 如果 chains.yml 未定义链路，回退到 group.yml 中的 chains 配置
        if not raw:
            group_chains = (self.meta_plugin.group_config or {}).get("chains")
            if group_chains:
                raw = group_chains

        normalized: Dict[str, Dict[str, Any]] = {}

        if isinstance(raw, dict):
            normalized = raw
        elif isinstance(raw, list):
            # 兼容旧格式：[{name, description, steps:[{plugin}]}]
            for item in raw:
                if not isinstance(item, dict):
                    self.logger.warning("Invalid chain entry (not a dict), skipped")
                    continue
                name = item.get("name")
                if not name or not isinstance(name, str):
                    self.logger.warning("Chain entry missing valid 'name', skipped")
                    continue
                desc = item.get("description") or ""
                steps = item.get("steps") or []
                if not isinstance(steps, list):
                    self.logger.warning(f"Chain '{name}' has invalid 'steps', skipped")
                    continue
                plugins: List[str] = []
                for step in steps:
                    if isinstance(step, dict) and step.get("plugin"):
                        plugins.append(str(step["plugin"]))
                    elif isinstance(step, str):
                        plugins.append(step)
                normalized[name] = {"description": desc, "plugins": plugins}
        else:
            self.logger.warning("Chains config has invalid type; expected dict or list, got %s", type(raw).__name__)
            normalized = {}

        for chain_name, chain_config in normalized.items():
            try:
                parsed_chain = self.parse_chain(chain_config if isinstance(chain_config, dict) else {})
                full_name = f"{self.meta_plugin.get_metadata().name}:{chain_name}"
                self.meta_plugin.chains[full_name] = parsed_chain
                self.logger.info(f"Registered chain: {full_name}")
            except Exception as e:
                self.logger.error(f"Failed to register chain '{chain_name}': {e}")
    
    def parse_chain(self, config: Dict[str, Any]) -> Dict[str, Any]:
        """解析链配置"""
        plugins = []
        
        for plugin_ref in config.get('plugins', []):
            if plugin_ref == "{next}":
                # 占位符，表示后续插件
                plugins.append("__NEXT__")
            elif plugin_ref in self.meta_plugin.subplugins:
                # 子插件引用
                plugins.append(f"{self.meta_plugin.get_metadata().name}.{plugin_ref}")
            else:
                # 外部插件引用
                plugins.append(plugin_ref)
        
        return {
            'pattern': config.get('pattern', ''),
            'description': config.get('description', ''),
            'plugins': plugins
        }
