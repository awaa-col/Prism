"""
Health check API endpoints.
"""

from typing import Dict, Any, List, Optional
import asyncio
from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from sqlalchemy import select, text

from app.api.deps import DBSession
from app.db.session import engine
from app.core.redis import get_redis
from app.plugins.loader import PluginLoader
from app.core.structured_logging import get_logger

router = APIRouter()
logger = get_logger("api.health")

# Global plugin loader instance
plugin_loader: Optional[PluginLoader] = None


def get_plugin_loader() -> PluginLoader:
    """Get the global plugin loader instance"""
    if not plugin_loader:
        raise RuntimeError("Plugin loader not initialized")
    return plugin_loader


@router.get("/health")
async def health_check(db: DBSession) -> Dict[str, Any]:
    """
    Basic health check endpoint.
    Returns the overall health status of the gateway.
    """
    health_status = {
        "status": "healthy",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "version": "2.0.0",
        "checks": {}
    }
    
    # Check database
    try:
        await db.execute(text("SELECT 1"))
        health_status["checks"]["database"] = {
            "status": "healthy",
            "message": "Database connection successful"
        }
    except Exception as e:
        health_status["status"] = "unhealthy"
        health_status["checks"]["database"] = {
            "status": "unhealthy",
            "message": f"Database error: {str(e)}"
        }
        logger.error("Database health check failed", error=str(e))
    
    # Check Redis (optional)
    from app.core.config import get_settings
    settings = get_settings()
    
    if settings.cache_backend == 'redis' and settings.redis.enabled:
        try:
            redis = get_redis()
            await redis.ping()
            health_status["checks"]["redis"] = {
                "status": "healthy",
                "message": "Redis connection successful"
            }
        except Exception as e:
            health_status["status"] = "degraded"  # 降级而不是不健康
            health_status["checks"]["redis"] = {
                "status": "unhealthy",
                "message": f"Redis error: {str(e)}"
            }
            logger.warning("Redis health check failed", error=str(e))
    else:
        health_status["checks"]["redis"] = {
            "status": "disabled",
            "message": "Redis backend disabled, using memory cache"
        }
    
    # Check plugin loader
    try:
        loader = get_plugin_loader()
        plugin_count = len(loader.get_all_plugins())
        health_status["checks"]["plugins"] = {
            "status": "healthy",
            "message": f"{plugin_count} plugins loaded",
            "plugin_count": plugin_count
        }
    except Exception as e:
        health_status["status"] = "degraded"
        health_status["checks"]["plugins"] = {
            "status": "unhealthy",
            "message": f"Plugin loader error: {str(e)}"
        }
        logger.error("Plugin loader health check failed", error=str(e))
    
    return health_status


@router.get("/health/plugins")
async def plugin_health_check() -> Dict[str, Any]:
    """
    Detailed health check for all loaded plugins.
    """
    try:
        loader = get_plugin_loader()
        plugins = loader.get_all_plugins()
        
        plugin_health = {
            "status": "healthy",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "total_plugins": len(plugins),
            "plugins": {}
        }
        
        # Check each plugin
        check_tasks = []
        plugin_names = []
        
        for name, plugin in plugins.items():
            plugin_names.append(name)
            
            async def check_plugin(p, n):
                try:
                    # Get plugin metadata
                    metadata = p.get_metadata()
                    
                    # Try to list models as a health check
                    models = await asyncio.wait_for(
                        p.list_models(),
                        timeout=5.0  # 5 second timeout
                    )
                    
                    return {
                        "name": n,
                        "status": "healthy",
                        "version": metadata.version,
                        "model_count": len(models),
                        "metadata": {
                            "description": metadata.description,
                            "author": metadata.author,
                            "requires_auth": metadata.requires_auth
                        }
                    }
                except asyncio.TimeoutError:
                    return {
                        "name": n,
                        "status": "timeout",
                        "error": "Plugin health check timed out"
                    }
                except Exception as e:
                    return {
                        "name": n,
                        "status": "unhealthy",
                        "error": str(e)
                    }
            
            check_tasks.append(check_plugin(plugin, name))
        
        # Run all checks concurrently
        if check_tasks:
            results = await asyncio.gather(*check_tasks, return_exceptions=True)
            
            for name, result in zip(plugin_names, results):
                if isinstance(result, Exception):
                    plugin_health["plugins"][name] = {
                        "status": "error",
                        "error": str(result)
                    }
                    plugin_health["status"] = "degraded"
                else:
                    plugin_health["plugins"][name] = result
                    if result["status"] != "healthy":
                        plugin_health["status"] = "degraded"
        
        return plugin_health
        
    except Exception as e:
        logger.error("Plugin health check failed", error=str(e), exc_info=True)
        return {
            "status": "error",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "error": str(e)
        }


@router.get("/health/ready")
async def readiness_check(db: DBSession) -> Dict[str, Any]:
    """
    Readiness probe for Kubernetes/container orchestration.
    Returns 200 if the service is ready to accept traffic.
    """
    # Quick checks only
    try:
        # Get settings
        from app.core.config import get_settings
        settings = get_settings()
        
        # Database check
        await db.execute(text("SELECT 1"))
        
        # Redis check (optional)
        if settings.cache_backend == 'redis' and settings.redis.enabled:
            redis = get_redis()
            await redis.ping()
        
        # Plugin loader check
        loader = get_plugin_loader()
        if len(loader.get_all_plugins()) == 0:
            return {
                "ready": False,
                "reason": "No plugins loaded"
            }
        
        return {"ready": True}
        
    except Exception as e:
        logger.error("Readiness check failed", error=str(e))
        return {
            "ready": False,
            "reason": str(e)
        }


@router.get("/health/live")
async def liveness_check() -> Dict[str, str]:
    """
    Liveness probe for Kubernetes/container orchestration.
    Simple check that the service is running.
    """
    return {"status": "alive"}


def set_plugin_loader(loader: PluginLoader) -> None:
    """Set the global plugin loader instance"""
    global plugin_loader
    plugin_loader = loader 