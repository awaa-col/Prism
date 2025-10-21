"""
Main application entry point.
"""

import asyncio
from typing import Optional
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from prometheus_client import make_asgi_app

import asyncio
from typing import Optional
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from prometheus_client import make_asgi_app

from app.core.config import get_settings
from app.core.structured_logging import setup_logging, get_logger
from app.core.startup import (
    perform_production_security_checks,
    init_core_services,
    init_plugins,
    shutdown_services,
)
from app.plugins.loader import PluginLoader
from app.middleware.logging import LoggingMiddleware
from app.middleware.telemetry import setup_telemetry

# Import routers
from app.api.v1.endpoints import admin
from app.api.v1 import health, identity, demo, oauth, account
from app.api.v1 import chain as chain_api
# Note: openai_compatible is now handled by ai_gateway plugin


# Get settings and logger
settings = get_settings()
setup_logging()
logger = get_logger("app.main")

# This is a placeholder for the plugin loader, which will be initialized in the lifespan.
plugin_loader: Optional[PluginLoader] = None

@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Application lifespan manager, now cleaner and more modular.
    """
    logger.info("Starting Prism Framework", version=settings.app_version)
    
    plugin_loader_instance = None
    try:
        perform_production_security_checks()
        await init_core_services()
        plugin_loader_instance = await init_plugins(app)
        
        logger.info("Application startup complete")
    except Exception as e:
        logger.error("Failed to start application", error=str(e), exc_info=True)
        # In a real scenario, you might want to exit or handle this more gracefully
        raise
        
    yield
    
    await shutdown_services(plugin_loader_instance)


# Create FastAPI app
app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    description="High-performance plugin framework with middleware support",
    lifespan=lifespan,
    docs_url="/docs" if settings.debug else None,
    redoc_url="/redoc" if settings.debug else None,
)

# Mount core static files
app.mount("/static", StaticFiles(directory="app/static"), name="static")

# Add metrics endpoint
metrics_app = make_asgi_app()
app.mount("/metrics", metrics_app)

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.server.cors["allow_origins"],
    allow_credentials=settings.server.cors["allow_credentials"],
    allow_methods=settings.server.cors["allow_methods"],
    allow_headers=settings.server.cors["allow_headers"],
)

# Add logging middleware
app.add_middleware(LoggingMiddleware)

# Setup telemetry
setup_telemetry(app)

# Include routers (chat and models now handled by plugins)

app.include_router(
    admin.router,
    prefix="/admin",
    tags=["Admin"] # This tag will be overridden by sub-routers
)

app.include_router(
    health.router,
    prefix="/api/v1",
    tags=["Health"]
)

app.include_router(
    identity.router,
    prefix="/api/v1/identity",
    tags=["Identity & Access Management"]
)

# 示例路由
app.include_router(
    demo.router,
    prefix="/api/v1/demo",
    tags=["Demo"]
)

# 新增：链式入口
app.include_router(
    chain_api.router,
    prefix="/api/v1/chain",
    tags=["Chain"]
)

# Note: OpenAI-compatible API is now handled by ai_gateway plugin
# The /v1/chat/completions endpoint is provided by the ai_gateway plugin

# OAuth (optional, provider adapters under app.api.v1.oauth_providers)
app.include_router(
    oauth.router,
    prefix="/api/v1",
    tags=["OAuth"]
)

# Static mount for /auth removed as project is now a pure API backend.

# 新增：账户自助 API Key 管理
app.include_router(
    account.router,
    prefix="/api/v1/account",
    tags=["Account"]
)



# 监控路由已移除（保留内部性能监控以供后续扩展）

# 移除重复的路由注册，只保留一套清晰的路由结构
# 静态页面通过静态文件服务器处理，不需要单独的路由

@app.get("/")
async def root():
    """Root endpoint"""
    return {
        "name": settings.app_name,
        "version": settings.app_version,
        "status": "running",
        "docs": "/docs" if settings.debug else None,
    }


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    """Global exception handler"""
    from app.utils.responses import APIResponse, APIException
    
    logger.error(
        "Unhandled exception",
        path=request.url.path,
        method=request.method,
        error=str(exc),
        exc_info=exc
    )
    
    # 如果是自定义API异常，使用其定义的状态码和消息
    if isinstance(exc, APIException):
        return APIResponse.error(
            message=exc.message,
            code=exc.code,
            status_code=exc.status_code,
            details=exc.details
        )
    
    # 其他异常统一处理
    return APIResponse.error(
        message="Internal server error",
        code="internal_error",
        status_code=500
    )


if __name__ == "__main__":
    import uvicorn
    
    uvicorn.run(
        "app.main:app",
        host=settings.server.host,
        port=settings.server.port,
        log_level=settings.server.log_level,
        reload=settings.debug,
        workers=1 if settings.debug else settings.server.workers,
    ) 