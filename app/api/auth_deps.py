"""
简化的认证依赖注入系统
"""

from typing import Any, Optional
from functools import lru_cache
from uuid import UUID

from fastapi import Depends, HTTPException, status, Header, Request, Cookie
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.db.session import get_db
from app.db.models import User, APIKey, Role
from app.core.security import decode_access_token
from app.core.security import hash_api_key
from app.core.cache import get_cache
from app.utils.responses import APIException
from app.core.structured_logging import get_logger

logger = get_logger("auth.deps")
security = HTTPBearer()

# 轻量虚拟 API Key 对象，避免 SQLAlchemy Column 类型与静态类型冲突
from dataclasses import dataclass
from typing import List

@dataclass
class VirtualAPIKey:
    id: str
    user_id: str
    is_active: bool = True
    rate_limit: Optional[int] = None
    rate_limit_period: int = 60
    expires_at: Optional[str] = None
    allowed_models: List[str] = None


class AuthManager:
    """统一的认证管理器"""
    
    def __init__(self):
        self.cache = get_cache(prefix="auth")
    
    @lru_cache(maxsize=1000)
    def create_virtual_api_key(self, user_id: str) -> VirtualAPIKey:
        """创建虚拟API密钥对象（缓存版本）"""
        return VirtualAPIKey(
            id=f"jwt-{user_id}",
            user_id=user_id,
            is_active=True,
            rate_limit=None,
            rate_limit_period=60,
            expires_at=None,
            allowed_models=[]
        )
    
    async def get_user_from_token(self, token: str, db: AsyncSession) -> Optional[User]:
        """从JWT令牌获取用户（带缓存和角色/权限预加载）"""
        cached_user_id = await self.cache.get(f"user_id_by_token:{token}")
        user_id_str = cached_user_id if isinstance(cached_user_id, str) else None

        if not user_id_str:
            payload = decode_access_token(token)
            if not payload: return None
            user_id_str = payload.get("user_id") or payload.get("sub")
            if not user_id_str: return None
            await self.cache.set(f"user_id_by_token:{token}", user_id_str, expire=3600)

        # 检查用户数据缓存是否失效
        if await self.is_user_cache_invalidated(user_id_str):
            await self.cache.delete(f"user_data:{user_id_str}")
            logger.debug("User data cache was invalidated, re-fetching", user_id=user_id_str)

        # 尝试从用户数据缓存中获取
        cached_user_data = await self.cache.get(f"user_data:{user_id_str}")
        if cached_user_data and isinstance(cached_user_data, dict):
            # 从缓存数据重建 User 对象（不完整，但足以用于权限检查）
            user = User(id=UUID(user_id_str), is_admin=cached_user_data.get("is_admin", False), is_active=True)
            # 注意：这里我们直接将缓存的权限集合附加到 user 对象上，绕过 ORM
            user._cached_permissions = set(cached_user_data.get("permissions", []))
            return user

        # 缓存未命中或失效，查询数据库
        result = await db.execute(
            select(User).options(
                selectinload(User.roles).selectinload(Role.permissions)
            ).where(User.id == user_id_str)
        )
        user = result.scalar_one_or_none()

        if user and user.is_active:
            # 汇总权限并更新缓存
            permission_set = {p.name for role in user.roles for p in role.permissions}
            user_data_to_cache = {
                "is_admin": user.is_admin,
                "permissions": list(permission_set),
            }
            await self.cache.set(f"user_data:{user_id_str}", user_data_to_cache, expire=300)
            await self.cache.delete(f"user_invalidated:{user_id_str}") # 清除失效标记
            user._cached_permissions = permission_set # 附加到本次请求的 user 对象上
            return user

        return None
    
    async def get_api_key_from_token(self, token: str, db: AsyncSession) -> Optional[APIKey]:
        """从API密钥令牌获取API密钥（带缓存, 使用哈希键避免明文泄漏）"""
        # 以哈希作为缓存键，避免暴露明文 token
        token_hash = hash_api_key(token)
        cached_key_data = await self.cache.get(f"apikey:{token_hash}")
        if cached_key_data:
            api_key_id = cached_key_data.get("id")
            result = await db.execute(select(APIKey).where(APIKey.id == api_key_id))
            api_key = result.scalar_one_or_none()
            if api_key and bool(getattr(api_key, "is_active", False)):
                return api_key
        
        # 数据库查询（按哈希等值匹配，DB 仅存哈希）
        result = await db.execute(select(APIKey).where(APIKey.key == token_hash))
        api_key = result.scalar_one_or_none()
        
        if api_key and bool(getattr(api_key, "is_active", False)):
            # 缓存API密钥信息5分钟
            key_data = {
                "id": str(api_key.id),
                "user_id": str(api_key.user_id),
                "rate_limit": api_key.rate_limit,
                "rate_limit_period": api_key.rate_limit_period
            }
            await self.cache.set(f"apikey:{token_hash}", key_data, expire=300)
            return api_key
            
        return None

    async def invalidate_user_cache(self, user_id: str) -> None:
        """清除指定用户的所有缓存项"""
        try:
            # 由于缓存键是 user:{token}，我们无法直接通过用户ID删除所有相关缓存
            # 但我们可以通过模式匹配来删除
            # 这里使用一个简单的方案：标记用户缓存已失效
            await self.cache.set(f"user_invalidated:{user_id}", True, expire=300)
            logger.info("User cache invalidated", user_id=user_id)
        except Exception as e:
            logger.warning("Failed to invalidate user cache", user_id=user_id, error=str(e))
    
    async def is_user_cache_invalidated(self, user_id: str) -> bool:
        """检查用户缓存是否已失效"""
        try:
            return await self.cache.get(f"user_invalidated:{user_id}") is not None
        except Exception:
            return False


# 全局认证管理器实例 - 延迟初始化
auth_manager = None

def get_auth_manager() -> AuthManager:
    """获取认证管理器实例（延迟初始化）"""
    global auth_manager
    if auth_manager is None:
        auth_manager = AuthManager()
    return auth_manager


async def get_current_user(
    request: Request,
    db: AsyncSession = Depends(get_db),
    session_id: Optional[str] = Cookie(None),
    authorization: Optional[str] = Header(None)
) -> Optional[User]:
    """
    Dependency to get the current user from either a session cookie or an Authorization header.
    """
    token = None
    if session_id:
        token = session_id
    elif authorization:
        parts = authorization.split()
        if len(parts) == 2 and parts[0].lower() == "bearer":
            token = parts[1]

    if token:
        return await get_auth_manager().get_user_from_token(token, db)
    
    return None


# --- 权限检查依赖函数 ---

async def require_user(
    current_user: Optional[User] = Depends(get_current_user)
) -> User:
    """依赖：要求用户必须经过认证。如果未认证，抛出 401 错误。"""
    if not current_user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication credentials"
        )
    return current_user

async def require_admin(
    current_user: User = Depends(require_user)
) -> User:
    """依赖：要求用户必须是管理员。建立在 require_user 之上。"""
    if not current_user.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin privileges required"
        )
    return current_user

def require_permission(permission_name: str):
    """
    依赖工厂：创建一个依赖项，用于检查用户是否拥有特定权限。
    - 管理员 (is_admin=True) 默认拥有所有权限。
    - 普通用户的权限来自于其所有角色的权限总和。
    """
    async def _permission_checker(
        current_user: User = Depends(require_user)
    ) -> User:
        # 1. 管理员直接通过
        if current_user.is_admin:
            return current_user

        # 2. 检查用户的权限集合 (来自缓存或数据库)
        user_permissions = getattr(current_user, '_cached_permissions', None)
        
        if user_permissions is None:
            # 如果缓存中没有，实时计算（这通常不应该发生，除非 get_user_from_token 逻辑有问题）
            logger.warning("Permissions not pre-loaded for user, calculating on-the-fly.", user_id=current_user.id)
            if not hasattr(current_user, 'roles'):
                 raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, "User object missing 'roles' attribute.")
            user_permissions = {p.name for role in current_user.roles for p in role.permissions}

        if permission_name not in user_permissions:
            logger.warning(
                "Permission denied for user",
                user_id=current_user.id,
                required_permission=permission_name,
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Permission '{permission_name}' required."
            )
        
        return current_user
    return _permission_checker

async def require_api_access(
    authorization: Optional[str] = Header(None),
    db: AsyncSession = Depends(get_db)
) -> Any:
    """依赖：要求有效的 API 访问凭证（JWT 或 API 密钥）。"""
    if not authorization:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authorization header required"
        )
    
    # 解析Authorization头
    parts = authorization.split()
    if len(parts) != 2 or parts[0].lower() != "bearer":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authorization header format"
        )
    
    token = parts[1]
    
    # 首先尝试JWT令牌
    user = await get_auth_manager().get_user_from_token(token, db)
    if user:
        return get_auth_manager().create_virtual_api_key(str(user.id))
    
    # 然后尝试API密钥
    api_key = await get_auth_manager().get_api_key_from_token(token, db)
    if api_key:
        return api_key
    
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid or expired token"
    )