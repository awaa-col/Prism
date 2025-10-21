from datetime import datetime, timezone
import time
from typing import Dict, Any

from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth_deps import require_admin
from app.db.session import get_db
from app.db.models import User
from app.core.structured_logging import get_logger

router = APIRouter()
logger = get_logger("api.admin.system")

@router.get("/status")
async def get_system_status(
    current_user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db)
):
    """Get comprehensive system status"""
    from app.core.redis import get_redis
    from app.main import plugin_loader
    try:
        import psutil
    except ImportError:
        psutil = None
    
    start_time = getattr(get_system_status, '_start_time', time.time())
    uptime_seconds = time.time() - start_time
    
    status_info: Dict[str, Any] = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "uptime": f"{int(uptime_seconds // 3600)}h {int((uptime_seconds % 3600) // 60)}m",
        "version": "2.0.0",
        "status": "healthy",
        "components": {}
    }
    
    # Database status
    try:
        await db.execute(text("SELECT 1"))
        status_info["components"]["database"] = {"status": "healthy", "message": "Connection successful"}
    except Exception as e:
        status_info["components"]["database"] = {"status": "unhealthy", "message": str(e)}
        status_info["status"] = "degraded"
    
    # Redis status
    try:
        redis = get_redis()
        await redis.ping()
        status_info["components"]["redis"] = {"status": "healthy", "message": "Connection successful"}
    except Exception as e:
        status_info["components"]["redis"] = {"status": "unhealthy", "message": str(e)}
        status_info["status"] = "degraded"
    
    # Plugin loader status
    if plugin_loader:
        plugins = plugin_loader.get_all_plugins()
        status_info["components"]["plugins"] = {
            "status": "healthy", "message": f"{len(plugins)} plugins loaded",
            "plugin_count": len(plugins), "plugins": list(plugins.keys())
        }
    else:
        status_info["components"]["plugins"] = {"status": "unhealthy", "message": "Plugin loader not initialized"}
        status_info["status"] = "degraded"
    
    # System resources
    if psutil:
        try:
            cpu_percent = psutil.cpu_percent(interval=0.1)
            memory = psutil.virtual_memory()
            disk = psutil.disk_usage('/')
            
            status_info["components"]["system"] = {
                "status": "healthy",
                "cpu_percent": cpu_percent,
                "memory_percent": memory.percent,
                "disk_percent": disk.percent,
                "memory_available_gb": memory.available / (1024**3),
                "disk_free_gb": disk.free / (1024**3)
            }
            
            if cpu_percent > 90 or memory.percent > 90 or disk.percent > 90:
                status_info["components"]["system"]["status"] = "degraded"
                status_info["status"] = "degraded"
        except Exception as e:
            status_info["components"]["system"] = {"status": "unknown", "message": str(e)}
    else:
        status_info["components"]["system"] = {"status": "unknown", "message": "psutil not available"}
    
    logger.info("System status requested", status=status_info["status"], user_id=str(current_user.id))
    return status_info