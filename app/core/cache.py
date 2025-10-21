"""
抽象缓存层 - 支持内存和Redis实现
设计原则：简单、高效、可扩展
"""

import json
import time
import asyncio
from abc import ABC, abstractmethod
from typing import Optional, Any, Dict, Tuple
from datetime import datetime, timedelta
from threading import RLock
from collections import OrderedDict

from app.core.config import get_settings
from app.core.structured_logging import get_logger

logger = get_logger("cache")
settings = get_settings()


class CacheInterface(ABC):
    """缓存接口抽象类"""
    
    @abstractmethod
    async def get(self, key: str) -> Optional[Any]:
        """获取缓存值"""
        pass
    
    @abstractmethod
    async def set(self, key: str, value: Any, expire: Optional[int] = None) -> None:
        """设置缓存值，expire为过期时间（秒）"""
        pass
    
    @abstractmethod
    async def delete(self, key: str) -> None:
        """删除缓存值"""
        pass
    
    @abstractmethod
    async def exists(self, key: str) -> bool:
        """检查键是否存在"""
        pass
    
    @abstractmethod
    async def increment(self, key: str, amount: int = 1) -> int:
        """递增计数器"""
        pass
    
    @abstractmethod
    async def expire(self, key: str, seconds: int) -> None:
        """设置过期时间"""
        pass


class RateLimiterInterface(ABC):
    """限流器接口抽象类"""
    
    @abstractmethod
    async def is_allowed(self, key: str, limit: int, period: int) -> Tuple[bool, int]:
        """检查是否允许请求，返回(是否允许, 剩余次数)"""
        pass
    
    @abstractmethod
    async def get_usage(self, key: str) -> int:
        """获取当前使用次数"""
        pass


class MemoryCache(CacheInterface):
    """高效的内存缓存实现"""
    
    def __init__(self, prefix: str = "cache", max_size: int = 10000):
        self.prefix = prefix
        self.max_size = max_size
        self._cache: OrderedDict = OrderedDict()
        self._expiry: Dict[str, float] = {}
        self._lock = RLock()
    
    def _make_key(self, key: str) -> str:
        """创建命名空间键"""
        return f"{self.prefix}:{key}"
    
    def _cleanup_expired(self) -> None:
        """清理过期键"""
        current_time = time.time()
        expired_keys = [
            key for key, expiry in self._expiry.items()
            if expiry <= current_time
        ]
        
        for key in expired_keys:
            self._cache.pop(key, None)
            self._expiry.pop(key, None)
    
    def _ensure_capacity(self) -> None:
        """确保缓存容量不超限"""
        while len(self._cache) >= self.max_size:
            # LRU淘汰：移除最老的键
            oldest_key = next(iter(self._cache))
            self._cache.pop(oldest_key, None)
            self._expiry.pop(oldest_key, None)
    
    async def get(self, key: str) -> Optional[Any]:
        """获取缓存值"""
        cache_key = self._make_key(key)
        
        with self._lock:
            self._cleanup_expired()
            
            if cache_key not in self._cache:
                return None
            
            # 更新访问顺序（LRU）
            value = self._cache.pop(cache_key)
            self._cache[cache_key] = value
            
            try:
                return json.loads(value) if isinstance(value, str) else value
            except (json.JSONDecodeError, TypeError):
                return value
    
    async def set(self, key: str, value: Any, expire: Optional[int] = None) -> None:
        """设置缓存值"""
        cache_key = self._make_key(key)
        
        with self._lock:
            self._cleanup_expired()
            self._ensure_capacity()
            
            # 序列化复杂对象
            if isinstance(value, (dict, list)):
                value = json.dumps(value)
            
            self._cache[cache_key] = value
            
            if expire:
                self._expiry[cache_key] = time.time() + expire
    
    async def delete(self, key: str) -> None:
        """删除缓存值"""
        cache_key = self._make_key(key)
        
        with self._lock:
            self._cache.pop(cache_key, None)
            self._expiry.pop(cache_key, None)
    
    async def exists(self, key: str) -> bool:
        """检查键是否存在"""
        cache_key = self._make_key(key)
        
        with self._lock:
            self._cleanup_expired()
            return cache_key in self._cache
    
    async def increment(self, key: str, amount: int = 1) -> int:
        """递增计数器"""
        cache_key = self._make_key(key)
        
        with self._lock:
            self._cleanup_expired()
            
            current_value = self._cache.get(cache_key, 0)
            if isinstance(current_value, str):
                try:
                    current_value = int(current_value)
                except ValueError:
                    current_value = 0
            
            new_value = current_value + amount
            self._cache[cache_key] = new_value
            
            return new_value
    
    async def expire(self, key: str, seconds: int) -> None:
        """设置过期时间"""
        cache_key = self._make_key(key)
        
        with self._lock:
            if cache_key in self._cache:
                self._expiry[cache_key] = time.time() + seconds


class MemoryRateLimiter(RateLimiterInterface):
    """高效的内存限流器实现"""
    
    def __init__(self, prefix: str = "rate_limit"):
        self.prefix = prefix
        self._counters: Dict[str, Dict[str, Any]] = {}
        self._lock = RLock()
    
    def _cleanup_expired(self) -> None:
        """清理过期的限流记录"""
        current_time = time.time()
        expired_keys = [
            key for key, data in self._counters.items()
            if data.get('expires_at', 0) <= current_time
        ]
        
        for key in expired_keys:
            self._counters.pop(key, None)
    
    async def is_allowed(self, key: str, limit: int, period: int) -> Tuple[bool, int]:
        """检查是否允许请求"""
        limiter_key = f"{self.prefix}:{key}"
        current_time = time.time()
        
        with self._lock:
            self._cleanup_expired()
            
            if limiter_key not in self._counters:
                # 首次请求
                self._counters[limiter_key] = {
                    'count': 1,
                    'expires_at': current_time + period
                }
                return True, limit - 1
            
            counter_data = self._counters[limiter_key]
            
            # 检查是否已过期
            if counter_data['expires_at'] <= current_time:
                # 重置计数器
                self._counters[limiter_key] = {
                    'count': 1,
                    'expires_at': current_time + period
                }
                return True, limit - 1
            
            # 递增计数
            counter_data['count'] += 1
            remaining = max(0, limit - counter_data['count'])
            is_allowed = counter_data['count'] <= limit
            
            return is_allowed, remaining
    
    async def get_usage(self, key: str) -> int:
        """获取当前使用次数"""
        limiter_key = f"{self.prefix}:{key}"
        
        with self._lock:
            self._cleanup_expired()
            counter_data = self._counters.get(limiter_key, {})
            return counter_data.get('count', 0)


# Redis实现（如果可用）
try:
    import redis.asyncio as redis
    from redis.asyncio.connection import ConnectionPool
    
    class RedisCache(CacheInterface):
        """Redis缓存实现"""
        
        def __init__(self, prefix: str = "cache", redis_client=None):
            self.prefix = prefix
            self.redis = redis_client
        
        def _make_key(self, key: str) -> str:
            return f"{self.prefix}:{key}"
        
        async def get(self, key: str) -> Optional[Any]:
            if not self.redis:
                return None
            
            value = await self.redis.get(self._make_key(key))
            if value:
                try:
                    return json.loads(value)
                except json.JSONDecodeError:
                    return value
            return None
        
        async def set(self, key: str, value: Any, expire: Optional[int] = None) -> None:
            if not self.redis:
                return
            
            if isinstance(value, (dict, list)):
                value = json.dumps(value)
            
            await self.redis.set(self._make_key(key), value, ex=expire)
        
        async def delete(self, key: str) -> None:
            if not self.redis:
                return
            await self.redis.delete(self._make_key(key))
        
        async def exists(self, key: str) -> bool:
            if not self.redis:
                return False
            return bool(await self.redis.exists(self._make_key(key)))
        
        async def increment(self, key: str, amount: int = 1) -> int:
            if not self.redis:
                return 0
            return await self.redis.incrby(self._make_key(key), amount)
        
        async def expire(self, key: str, seconds: int) -> None:
            if not self.redis:
                return
            await self.redis.expire(self._make_key(key), seconds)
    
    
    class RedisRateLimiter(RateLimiterInterface):
        """Redis限流器实现"""
        
        def __init__(self, prefix: str = "rate_limit", redis_client=None):
            self.prefix = prefix
            self.redis = redis_client
        
        async def is_allowed(self, key: str, limit: int, period: int) -> Tuple[bool, int]:
            if not self.redis:
                return True, limit
            
            redis_key = f"{self.prefix}:{key}"
            current = await self.redis.incr(redis_key)
            
            if current == 1:
                await self.redis.expire(redis_key, period)
            
            remaining = max(0, limit - current)
            is_allowed = current <= limit
            
            return is_allowed, remaining
        
        async def get_usage(self, key: str) -> int:
            if not self.redis:
                return 0
            
            redis_key = f"{self.prefix}:{key}"
            usage = await self.redis.get(redis_key)
            return int(usage) if usage else 0

except ImportError:
    # Redis不可用时的占位符
    class RedisCache(CacheInterface):
        def __init__(self, *args, **kwargs):
            logger.warning("Redis not available, using memory cache instead")
        
        async def get(self, key: str) -> Optional[Any]:
            return None
        async def set(self, key: str, value: Any, expire: Optional[int] = None) -> None:
            pass
        async def delete(self, key: str) -> None:
            pass
        async def exists(self, key: str) -> bool:
            return False
        async def increment(self, key: str, amount: int = 1) -> int:
            return 0
        async def expire(self, key: str, seconds: int) -> None:
            pass
    
    class RedisRateLimiter(RateLimiterInterface):
        def __init__(self, *args, **kwargs):
            logger.warning("Redis not available, using memory rate limiter instead")
        
        async def is_allowed(self, key: str, limit: int, period: int) -> Tuple[bool, int]:
            return True, limit
        async def get_usage(self, key: str) -> int:
            return 0


# 全局缓存和限流器实例
_cache_instance: Optional[CacheInterface] = None
_rate_limiter_instance: Optional[RateLimiterInterface] = None


def get_cache(prefix: str = "cache") -> CacheInterface:
    """获取缓存实例"""
    global _cache_instance
    
    if _cache_instance is None:
        # 根据配置选择实现
        cache_backend = getattr(settings, 'cache_backend', 'memory')
        
        if cache_backend == 'redis':
            try:
                from app.core.redis import get_redis
                redis_client = get_redis()
                _cache_instance = RedisCache(prefix, redis_client)
                logger.info("Using Redis cache backend")
            except Exception as e:
                logger.warning(f"Failed to initialize Redis cache: {e}, falling back to memory")
                _cache_instance = MemoryCache(prefix)
        else:
            _cache_instance = MemoryCache(prefix)
            logger.info("Using memory cache backend")
    
    return _cache_instance


def get_rate_limiter(prefix: str = "rate_limit") -> RateLimiterInterface:
    """获取限流器实例"""
    global _rate_limiter_instance
    
    if _rate_limiter_instance is None:
        # 根据配置选择实现
        cache_backend = getattr(settings, 'cache_backend', 'memory')
        
        if cache_backend == 'redis':
            try:
                from app.core.redis import get_redis
                redis_client = get_redis()
                _rate_limiter_instance = RedisRateLimiter(prefix, redis_client)
                logger.info("Using Redis rate limiter backend")
            except Exception as e:
                logger.warning(f"Failed to initialize Redis rate limiter: {e}, falling back to memory")
                _rate_limiter_instance = MemoryRateLimiter(prefix)
        else:
            _rate_limiter_instance = MemoryRateLimiter(prefix)
            logger.info("Using memory rate limiter backend")
    
    return _rate_limiter_instance 