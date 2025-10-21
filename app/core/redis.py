"""
Redis connection management and utilities.
"""

import json
from typing import Optional, Any, Dict
from datetime import timedelta

import redis.asyncio as redis
from redis.asyncio.connection import ConnectionPool

from app.core.config import get_settings

settings = get_settings()

# Global Redis client
redis_client: Optional[redis.Redis] = None
connection_pool: Optional[ConnectionPool] = None


async def init_redis() -> None:
    """Initialize Redis connection"""
    global redis_client, connection_pool
    
    connection_pool = ConnectionPool.from_url(
        settings.redis.url,
        max_connections=settings.redis.pool_size,
        decode_responses=settings.redis.decode_responses
    )
    
    redis_client = redis.Redis(connection_pool=connection_pool)
    
    # Test connection
    await redis_client.ping()


async def close_redis() -> None:
    """Close Redis connection"""
    global redis_client, connection_pool
    
    if redis_client:
        await redis_client.close()
        redis_client = None
    
    if connection_pool:
        await connection_pool.disconnect()
        connection_pool = None


def get_redis() -> redis.Redis:
    """Get Redis client instance"""
    if not redis_client:
        raise RuntimeError("Redis not initialized. Call init_redis() first.")
    return redis_client


class RedisCache:
    """High-level cache operations"""
    
    def __init__(self, prefix: str = "cache"):
        self.prefix = prefix
        self.redis = get_redis()
    
    def _make_key(self, key: str) -> str:
        """Create a namespaced key"""
        return f"{self.prefix}:{key}"
    
    async def get(self, key: str) -> Optional[Any]:
        """Get value from cache"""
        value = await self.redis.get(self._make_key(key))
        if value:
            try:
                return json.loads(value)
            except json.JSONDecodeError:
                return value
        return None
    
    async def set(
        self, 
        key: str, 
        value: Any, 
        expire: Optional[int] = None
    ) -> None:
        """Set value in cache with optional expiration (in seconds)"""
        if isinstance(value, (dict, list)):
            value = json.dumps(value)
        
        await self.redis.set(
            self._make_key(key),
            value,
            ex=expire
        )
    
    async def delete(self, key: str) -> None:
        """Delete value from cache"""
        await self.redis.delete(self._make_key(key))
    
    async def exists(self, key: str) -> bool:
        """Check if key exists"""
        return bool(await self.redis.exists(self._make_key(key)))
    
    async def increment(self, key: str, amount: int = 1) -> int:
        """Increment a counter"""
        return await self.redis.incrby(self._make_key(key), amount)
    
    async def expire(self, key: str, seconds: int) -> None:
        """Set expiration on a key"""
        await self.redis.expire(self._make_key(key), seconds)


class RateLimiter:
    """Redis-based rate limiter"""
    
    def __init__(self, prefix: str = "rate_limit"):
        self.prefix = prefix
        self.redis = get_redis()
    
    async def is_allowed(
        self,
        key: str,
        limit: int,
        period: int  # in seconds
    ) -> tuple[bool, int]:
        """
        Check if request is allowed under rate limit.
        Returns (is_allowed, remaining_requests)
        """
        redis_key = f"{self.prefix}:{key}"
        
        # Use a sliding window approach
        current = await self.redis.incr(redis_key)
        
        if current == 1:
            # First request, set expiration
            await self.redis.expire(redis_key, period)
        
        remaining = max(0, limit - current)
        is_allowed = current <= limit
        
        return is_allowed, remaining
    
    async def get_usage(self, key: str) -> int:
        """Get current usage count"""
        redis_key = f"{self.prefix}:{key}"
        usage = await self.redis.get(redis_key)
        return int(usage) if usage else 0 