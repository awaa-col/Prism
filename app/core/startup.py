"""
Application startup and shutdown event handlers.
This module helps to keep the main.py file clean by separating the complex
initialization and shutdown logic.
"""

import os
from fastapi import FastAPI

from app.core.config import get_settings
from app.core.oauth_registry import get_oauth_provider_registry
from app.core.structured_logging import get_logger
from app.core.redis import init_redis, close_redis
from app.db.session import init_db, close_db, AsyncSessionLocal
from app.db.models import User
from app.core.security import get_password_hash
from app.plugins.loader import PluginLoader
from app.core.chain_runner import ChainRunner
from app.core.runtime import set_plugin_loader as rt_set_plugin_loader, set_chain_runner as rt_set_chain_runner
from app.services.plugin_manager import initialize_plugin_manager
from app.api.v1 import health

settings = get_settings()
logger = get_logger("app.startup")

def perform_production_security_checks():
    """Performs security baseline checks in a production environment."""
    strict_mode = os.environ.get("PRISM_STRICT_PROD", "0") == "1"
    if settings.debug:
        return
    issues = []
    if settings.security.secret_key == "dev-secret-key-change-in-production":
        issues.append("SECURITY: SECRET_KEY is set to its default value.")
    cors = settings.server.cors or {}
    if cors.get("allow_credentials") and ("*" in cors.get("allow_origins", [])):
        issues.append("SECURITY: CORS allows credentials with wildcard origin.")
    if settings.cache_backend != 'redis' or not settings.redis.enabled:
        issues.append("SECURITY: Redis cache is not enabled in production.")
    if settings.admin_password == "changeme":
        issues.append("SECURITY: Default admin password is not changed.")
    
    if issues:
        for msg in issues:
            logger.error(msg)
        if strict_mode:
            raise RuntimeError("Production security baseline check failed.")
        else:
            logger.warning("Production security checks found issues (non-strict mode).")

async def create_default_admin_user():
    """Creates the default admin user if it doesn't exist."""
    from sqlalchemy import select
    async with AsyncSessionLocal() as db:
        result = await db.execute(select(User).where(User.username == settings.admin_username))
        if not result.scalar_one_or_none():
            logger.info("Creating default admin user...")
            admin_user = User(
                username=settings.admin_username,
                email=f"{settings.admin_username}@localhost",
                hashed_password=get_password_hash(settings.admin_password),
                is_admin=True,
                is_active=True
            )
            db.add(admin_user)
            await db.commit()
            logger.info("Default admin user created.")

async def init_core_services():
    """Initializes core services like database, cache, and OAuth providers."""
    logger.info("Initializing database...")
    await init_db()
    
    logger.info("Loading OAuth providers...")
    get_oauth_provider_registry().load_providers()
    
    await create_default_admin_user()
    
    if settings.cache_backend == 'redis' and settings.redis.enabled:
        try:
            logger.info("Initializing Redis...")
            await init_redis()
            logger.info("Redis initialized successfully.")
        except Exception as e:
            logger.warning(f"Failed to initialize Redis: {e}. Continuing without cache.")

async def init_plugins(app: FastAPI) -> PluginLoader:
    """Initializes and loads all plugins, returning the loader instance."""
    logger.info("Initializing plugin loader...")
    plugin_loader = PluginLoader(app=app)
    await plugin_loader.initialize()
    rt_set_plugin_loader(plugin_loader)
    app.state.plugin_loader = plugin_loader
    
    # Initialize the service layer that depends on the loader
    initialize_plugin_manager(plugin_loader)
    
    logger.info("Loading plugins...")
    await plugin_loader.load_all_plugins()
    
    logger.info("Initializing chain runner...")
    chain_runner = ChainRunner(plugin_loader.get_all_plugins())
    rt_set_chain_runner(chain_runner)
    
    health.set_plugin_loader(plugin_loader)
    
    # Dynamically mount plugin routers
    plugin_routers = await plugin_loader.get_all_routers()
    reserved_prefixes = ("/api", "/admin", "/docs", "/redoc", "/metrics")
    
    for plugin_name, router in plugin_routers.items():
        prefix = f"/plugins/{plugin_name}"
        for route in router.routes:
            if hasattr(route, "path"):
                full_path = f"{prefix}{route.path}"
                if any(full_path.startswith(reserved) for reserved in reserved_prefixes):
                    logger.error("Plugin attempts to override a reserved route. Blocking.", plugin=plugin_name, route=full_path)
                    continue
        app.include_router(router, prefix=prefix, tags=[f"Plugin-{plugin_name}"])
        logger.info("Mounted plugin router", plugin=plugin_name, prefix=prefix)
        
    return plugin_loader

async def shutdown_services(plugin_loader: PluginLoader | None):
    """Handles graceful shutdown of all services."""
    logger.info("Shutting down Prism Framework...")
    if plugin_loader:
        await plugin_loader.shutdown()
    
    try:
        await close_redis()
    except Exception as e:
        logger.warning(f"Error closing Redis: {e}")
        
    await close_db()
    logger.info("Application shutdown complete.")