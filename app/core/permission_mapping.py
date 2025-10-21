# -*- coding: utf-8 -*-
"""
权限映射表 - 将插件的旧权限格式转换为新的标准格式
"""
from typing import Optional, Tuple, Dict
from app.core.permission_registry import get_permission_info

# 旧权限格式 -> 新权限格式映射表
# 格式: (old_type, old_resource_pattern) -> (new_type, new_resource_or_None)
PERMISSION_MIGRATION_MAP: Dict[Tuple[str, Optional[str]], Tuple[str, Optional[str]]] = {
    # 文件权限
    ("file", "read"): ("file.read", None),
    ("file", "write"): ("file.write", None),
    
    # API 权限
    ("api", "create_route"): ("api.route", None),
    
    # 网络权限 - HTTP 方法特定
    ("network.http_get", "/api/v1/*"): ("api.call", None),
    ("network.http_post", "/api/v1/*"): ("api.call", None),
    ("network.http_put", "/api/v1/*"): ("api.call", None),
    ("network.http_patch", "/api/v1/*"): ("api.call", None),
    ("network.http_delete", "/api/v1/*"): ("api.call", None),
    
    # 网络权限 - 通用
    ("network", "outbound:http"): ("network.http", None),
    ("network", "outbound:https"): ("network.https", None),
    
    # 系统权限
    ("system", "subprocess"): ("admin.subprocess", None),
    ("database", "access"): ("admin.database", None),
}


def normalize_permission(old_type: str, old_resource: Optional[str]) -> Tuple[str, Optional[str], str]:
    """
    将旧权限格式转换为新格式
    
    Args:
        old_type: 旧权限类型 (如 "network.http_get", "api")
        old_resource: 旧资源限定 (如 "/api/v1/*", "create_route")
    
    Returns:
        (new_type, new_resource, warning_message)
        - new_type: 新权限类型
        - new_resource: 新资源限定 (可能为 None)
        - warning_message: 警告信息 (如果有兼容性问题)
    """
    warning = ""
    
    # 1. 尝试精确匹配
    key = (old_type.lower(), old_resource)
    if key in PERMISSION_MIGRATION_MAP:
        new_type, new_resource = PERMISSION_MIGRATION_MAP[key]
        return new_type, new_resource, ""
    
    # 2. 尝试只匹配类型 (resource 为通配)
    key_wildcard = (old_type.lower(), None)
    if key_wildcard in PERMISSION_MIGRATION_MAP:
        new_type, _ = PERMISSION_MIGRATION_MAP[key_wildcard]
        # 保留原始 resource
        return new_type, old_resource, ""
    
    # 3. 检查是否已经是新格式
    perm_info = get_permission_info(old_type.lower())
    if perm_info:
        # 已经是新格式,直接返回
        return old_type.lower(), old_resource, ""
    
    # 4. 无法识别的权限
    warning = f"未识别的权限格式: {old_type}:{old_resource or 'N/A'}. 请查阅权限文档更新插件声明。"
    # 返回原始值,让后续流程决定是否拒绝
    return old_type.lower(), old_resource, warning


def validate_permission_scope(perm_type: str, resource: Optional[str]) -> Tuple[bool, Optional[str]]:
    """
    验证权限的作用域是否合法
    
    Args:
        perm_type: 权限类型 (新格式,如 "file.read", "admin.file.write")
        resource: 资源限定
    
    Returns:
        (is_valid, error_message)
    """
    perm_info = get_permission_info(perm_type)
    if not perm_info:
        return False, f"权限类型 '{perm_type}' 未在系统中注册"
    
    # 基础级别权限的额外限制
    if perm_info.tier.value == "basic":
        # file.read/write 必须限定在 plugin_data 目录
        if perm_type in ("file.read", "file.write"):
            if resource and not resource.startswith("plugin_data/"):
                return False, f"基础级权限 '{perm_type}' 只能访问 plugin_data/<plugin_name>/ 目录,当前声明: {resource}"
        
        # api.call 必须限定在 /api/v1 且排除 /admin
        if perm_type == "api.call":
            if resource and ("/admin" in resource or not resource.startswith("/api/v1")):
                return False, f"基础级权限 'api.call' 只能访问 /api/v1/* (排除 /admin),当前声明: {resource}"
        
        # network.http/https 必须指定域名
        if perm_type in ("network.http", "network.https"):
            if not resource:
                return False, f"基础级权限 '{perm_type}' 必须指定允许的域名白名单"
    
    return True, None


def get_migration_suggestions(old_permissions: list) -> list:
    """
    为旧权限格式提供迁移建议
    
    Args:
        old_permissions: 旧权限列表 [{"type": ..., "resource": ...}, ...]
    
    Returns:
        建议列表 [{"old": ..., "new": ..., "reason": ...}, ...]
    """
    suggestions = []
    
    for perm in old_permissions:
        old_type = perm.get("type", "")
        old_resource = perm.get("resource")
        
        new_type, new_resource, warning = normalize_permission(old_type, old_resource)
        
        if warning or (old_type.lower() != new_type or old_resource != new_resource):
            suggestions.append({
                "old": f"{old_type}:{old_resource or 'N/A'}",
                "new": f"{new_type}:{new_resource or 'N/A'}",
                "reason": warning or "权限格式已更新,建议使用新格式",
                "action": "auto_converted" if not warning else "needs_review"
            })
    
    return suggestions


