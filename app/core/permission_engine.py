# -*- coding: utf-8 -*-
"""
一个优雅且解耦的插件权限引擎。

此模块取代了硬编码在 audit_sandbox.py 中的权限检查逻辑，
提供了一个中心化的、基于规则的权限定义与匹配系统。
"""
from typing import Dict, Optional, Callable, Any, List, Tuple
import fnmatch
from dataclasses import dataclass, field

# --- 权限定义 ---

@dataclass
class PermissionDefinition:
    """
    定义一个权限及其匹配规则。
    """
    name: str  # 权限的唯一名称，例如 "file.write.plugin"
    description: str  # 权限的描述
    match_type: str
    event_names: List[str]
    
    # 匹配 plugin.yml 中的声明
    match_resource_pattern: Optional[str] = None
    
    # 运行时事件匹配逻辑
    resource_matcher: Callable[[Any], bool] = field(default=lambda _: True)

# --- 权限注册表与引擎 ---

class PermissionEngine:
    """
    管理所有权限定义，并根据运行时事件匹配相应的权限。
    """
    def __init__(self):
        self._permissions: Dict[str, PermissionDefinition] = {}
        self._event_map: Dict[str, List[PermissionDefinition]] = {}
        self._register_default_permissions()

    def register(self, perm_def: PermissionDefinition):
        """注册一个新的权限定义"""
        if perm_def.name in self._permissions:
            raise ValueError(f"Permission '{perm_def.name}' is already registered.")
        
        self._permissions[perm_def.name] = perm_def
        for event in perm_def.event_names:
            if event not in self._event_map:
                self._event_map[event] = []
            self._event_map[event].append(perm_def)

    def get_permission_definition(self, name: str) -> Optional[PermissionDefinition]:
        """根据名称获取权限定义"""
        return self._permissions.get(name)

    def is_valid_permission_type(self, perm_type: str) -> bool:
        """检查一个权限类型是否存在于任何已注册的权限定义中。"""
        return any(p.match_type == perm_type for p in self._permissions.values())

    def map_event_to_permissions(self, event: str, args: Tuple[Any, ...]) -> List[Tuple[PermissionDefinition, Any]]:
        """
        根据审计事件和参数，匹配可能需要的权限定义列表。
        返回一个元组列表 (PermissionDefinition, matched_resource)。
        """
        potential_perms = self._event_map.get(event, [])
        if not potential_perms:
            # 对于 os.system, subprocess.* 等动态事件名称
            for key in self._event_map:
                if event.startswith(key):
                    potential_perms.extend(self._event_map[key])

        matched: List[Tuple[PermissionDefinition, Any]] = []
        for perm in potential_perms:
            # 根据事件类型，将正确的参数传递给 resource_matcher
            resource_to_match = None
            resource_to_return = args[0] if args else None

            if event == "open" and len(args) > 1:
                resource_to_match = args[1] # 对于 'open'，我们匹配模式 (mode)
            elif event == "socket.connect" and len(args) > 1:
                 resource_to_match = args[1] # 对于 'socket.connect'，我们匹配地址元组
            elif event.startswith("subprocess.") and args:
                 resource_to_match = args[0] # 对于子进程，我们匹配命令列表
            elif args:
                resource_to_match = args[0]
            
            if perm.resource_matcher(resource_to_match):
                matched.append((perm, resource_to_return))
        
        return matched
    
    def find_definition_for_declaration(self, decl_type: str, decl_resource: Optional[str]) -> Optional[PermissionDefinition]:
        """
        根据 plugin.yml 中的声明，找到对应的权限定义。
        """
        for perm in self._permissions.values():
            if perm.match_type == decl_type:
                if perm.match_resource_pattern:
                    if decl_resource and fnmatch.fnmatch(decl_resource, perm.match_resource_pattern):
                        return perm
                # 如果 pattern 是 None，意味着它匹配该 type 下的所有资源
                elif perm.match_resource_pattern is None:
                    return perm
        return None

    def _register_default_permissions(self):
        """在这里集中注册系统内置的所有权限"""
        
        # --- 文件权限 ---
        self.register(PermissionDefinition(
            name="file.read.plugin",
            description="允许读取插件自身目录内的文件。",
            match_type="file",
            event_names=["open"],
            match_resource_pattern="read",
            # 这是一个简化的示例，完整的路径检查逻辑仍在 audit_sandbox 中
            resource_matcher=lambda mode: 'r' in mode
        ))

        self.register(PermissionDefinition(
            name="file.write.plugin",
            description="允许写入插件自身目录内的文件。",
            match_type="file",
            event_names=["open"],
            match_resource_pattern="write",
            resource_matcher=lambda mode: any(c in mode for c in ('w', 'a', '+'))
        ))
        
        # --- 网络权限 ---
        self.register(PermissionDefinition(
            name="network.https",
            description="允许通过 HTTPS (端口 443) 访问外部网络。",
            match_type="network",
            event_names=["socket.connect"],
            match_resource_pattern="outbound:https",
            resource_matcher=lambda addr: isinstance(addr, (tuple, list)) and len(addr) > 1 and addr[1] == 443
        ))
        
        self.register(PermissionDefinition(
            name="network.http",
            description="允许通过 HTTP (端口 80) 访问外部网络。",
            match_type="network",
            event_names=["socket.connect"],
            match_resource_pattern="outbound:http",
            resource_matcher=lambda addr: isinstance(addr, (tuple, list)) and len(addr) > 1 and addr[1] == 80
        ))
        
        # --- API 路由注册权限（用于挂载插件路由的资格）---
        self.register(PermissionDefinition(
            name="api.create_route",
            description="允许插件注册自己的 API 路由。",
            match_type="api",
            event_names=[],
            match_resource_pattern="create_route",
            resource_matcher=lambda _: True
        ))

        # --- 子进程权限 ---
        self.register(PermissionDefinition(
            name="system.subprocess",
            description="允许执行子进程和系统命令。",
            match_type="system",
            event_names=["os.system", "subprocess."], # 使用前缀匹配
            match_resource_pattern="subprocess",
            resource_matcher=lambda _: True # 匹配所有命令
        ))

# --- 单例 ---
_permission_engine_instance: Optional[PermissionEngine] = None

def get_permission_engine() -> PermissionEngine:
    """获取 PermissionEngine 的单例实例"""
    global _permission_engine_instance
    if _permission_engine_instance is None:
        _permission_engine_instance = PermissionEngine()
    return _permission_engine_instance
