# -*- coding: utf-8 -*-
"""
权限注册表 - 定义所有可用权限及其说明、风险等级、作用域限制
"""
from dataclasses import dataclass
from enum import Enum
from typing import Optional, Dict, List


class RiskLevel(Enum):
    """权限风险等级"""
    LOW = "low"          # 低风险:仅影响插件自身数据
    MEDIUM = "medium"    # 中风险:可能影响用户数据或服务
    HIGH = "high"        # 高风险:可能影响系统或其他插件
    CRITICAL = "critical"  # 严重:可能完全控制系统


class PermissionTier(Enum):
    """权限层级"""
    BASIC = "basic"      # 基础级:受限的插件权限
    ADMIN = "admin"      # 管理员级:更高特权


@dataclass
class PermissionInfo:
    """
    权限信息定义
    
    Attributes:
        name: 权限名称(如 file.read)
        tier: 权限层级(basic/admin)
        risk_level: 风险等级
        kernel_description: 内核对该权限的官方说明
        scope_limit: 作用域限制说明
        examples: 使用示例
    """
    name: str
    tier: PermissionTier
    risk_level: RiskLevel
    kernel_description: str
    scope_limit: str
    examples: List[str]


# 全局权限注册表
PERMISSION_REGISTRY: Dict[str, PermissionInfo] = {}


def register_permission(perm: PermissionInfo):
    """注册一个权限到全局注册表"""
    PERMISSION_REGISTRY[perm.name] = perm


def get_permission_info(perm_name: str) -> Optional[PermissionInfo]:
    """获取权限信息"""
    return PERMISSION_REGISTRY.get(perm_name)


# ========== 基础级权限 ==========

register_permission(PermissionInfo(
    name="file.read",
    tier=PermissionTier.BASIC,
    risk_level=RiskLevel.LOW,
    kernel_description="允许插件读取文件。基础级仅限读取 plugin_data/<plugin_name>/ 目录下的文件。",
    scope_limit="plugin_data/<plugin_name>/",
    examples=["plugin_data/my_plugin/config.json", "plugin_data/my_plugin/cache/"]
))

register_permission(PermissionInfo(
    name="file.write",
    tier=PermissionTier.BASIC,
    risk_level=RiskLevel.MEDIUM,
    kernel_description="允许插件写入文件。基础级仅限写入 plugin_data/<plugin_name>/ 目录下的文件。禁止访问 .lock 文件。",
    scope_limit="plugin_data/<plugin_name>/ (排除 *.lock)",
    examples=["plugin_data/my_plugin/data.db", "plugin_data/my_plugin/logs/"]
))

register_permission(PermissionInfo(
    name="api.call",
    tier=PermissionTier.BASIC,
    risk_level=RiskLevel.LOW,
    kernel_description="允许插件调用内核 API。基础级仅限调用 /api/v1/* 下的非管理员端点。",
    scope_limit="/api/v1/* (排除 /api/v1/admin/*)",
    examples=["/api/v1/identity/me", "/api/v1/demo/test"]
))

register_permission(PermissionInfo(
    name="api.route",
    tier=PermissionTier.BASIC,
    risk_level=RiskLevel.MEDIUM,
    kernel_description="允许插件注册自己的 API 路由。基础级路由注册在 /api/v1/plugins/<plugin_name>/* 下。",
    scope_limit="/api/v1/plugins/<plugin_name>/*",
    examples=["/api/v1/plugins/my_plugin/webhook", "/api/v1/plugins/my_plugin/status"]
))

register_permission(PermissionInfo(
    name="network.http",
    tier=PermissionTier.BASIC,
    risk_level=RiskLevel.MEDIUM,
    kernel_description="允许插件通过 HTTP 协议访问外部网络(端口80)。需要配合 resource 指定域名白名单。",
    scope_limit="指定的域名白名单",
    examples=["resource: api.example.com", "resource: *.github.com"]
))

register_permission(PermissionInfo(
    name="network.https",
    tier=PermissionTier.BASIC,
    risk_level=RiskLevel.MEDIUM,
    kernel_description="允许插件通过 HTTPS 协议访问外部网络(端口443)。需要配合 resource 指定域名白名单。",
    scope_limit="指定的域名白名单",
    examples=["resource: api.example.com", "resource: *.github.com"]
))

# ========== 管理员级权限 ==========

register_permission(PermissionInfo(
    name="admin.file.read",
    tier=PermissionTier.ADMIN,
    risk_level=RiskLevel.HIGH,
    kernel_description="允许插件读取项目根目录下的任意文件(除 .lock 文件)。这是管理员级权限,可能访问敏感配置。",
    scope_limit="项目根目录 (排除 *.lock)",
    examples=["config.yml", "plugins/*/plugin.yml", "app/core/config.py"]
))

register_permission(PermissionInfo(
    name="admin.file.write",
    tier=PermissionTier.ADMIN,
    risk_level=RiskLevel.CRITICAL,
    kernel_description="允许插件写入项目根目录下的任意文件(除 .lock 文件)。这是管理员级权限,可能修改系统配置或代码。",
    scope_limit="项目根目录 (排除 *.lock)",
    examples=["config.yml", "plugins/custom_plugin/config.json"]
))

register_permission(PermissionInfo(
    name="admin.api.call",
    tier=PermissionTier.ADMIN,
    risk_level=RiskLevel.HIGH,
    kernel_description="允许插件调用所有内核 API,包括管理员端点(如 /admin/*)。可执行系统管理操作。",
    scope_limit="无限制",
    examples=["/admin/plugins/install", "/admin/rbac/roles"]
))

register_permission(PermissionInfo(
    name="admin.api.route",
    tier=PermissionTier.ADMIN,
    risk_level=RiskLevel.HIGH,
    kernel_description="允许插件在根路由(/)注册自定义路由。可能覆盖系统路由或创建公开端点。",
    scope_limit="无限制,可注册到根路由 /*",
    examples=["/custom-plugin/api", "/webhook/github"]
))

register_permission(PermissionInfo(
    name="admin.net",
    tier=PermissionTier.ADMIN,
    risk_level=RiskLevel.HIGH,
    kernel_description="允许插件访问任意外部网络(HTTP/HTTPS/其他协议),无域名限制。可能用于数据外泄或攻击。",
    scope_limit="无限制",
    examples=["任意 HTTP/HTTPS 请求", "自定义协议"]
))

register_permission(PermissionInfo(
    name="admin.subprocess",
    tier=PermissionTier.ADMIN,
    risk_level=RiskLevel.CRITICAL,
    kernel_description="允许插件执行任意系统命令和子进程。这是最高风险权限,可能完全控制服务器。",
    scope_limit="无限制",
    examples=["os.system('ls')", "subprocess.run(['git', 'clone', '...'])"]
))

register_permission(PermissionInfo(
    name="admin.database",
    tier=PermissionTier.ADMIN,
    risk_level=RiskLevel.CRITICAL,
    kernel_description="允许插件直接访问系统数据库。可读取、修改、删除所有数据,包括用户凭证。",
    scope_limit="无限制",
    examples=["直接 SQL 查询", "ORM 模型访问"]
))


# ========== 禁止的权限 ==========
# 注意:以下权限永远不应该被授予,这里仅作为文档记录

"""
FORBIDDEN:
- 任何涉及 .lock 文件的访问(读/写/删除)
- 直接修改其他插件的 plugin_data
- 访问系统密钥存储(如 .env, secrets/)
"""


