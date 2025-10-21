"""
Structured logging configuration using structlog.
"""

import logging
import sys
import os
from logging import handlers
from typing import Any, Dict

import structlog
from structlog.dev import ConsoleRenderer, RichTracebackFormatter
from structlog.processors import CallsiteParameter

from app.core.config import get_settings

settings = get_settings()


SENSITIVE_FIELDS = {"authorization", "api-key", "apikey", "x-api-key", "password", "passwd", "secret", "token"}


class AppLogFilter(logging.Filter):
    """只允许非插件日志通过"""
    def filter(self, record):
        return not record.name.startswith('plugin')

class PluginLogFilter(logging.Filter):
    """只允许插件日志通过"""
    def filter(self, record):
        return record.name.startswith('plugin')


def _mask_sensitive(data: Dict[str, Any]) -> Dict[str, Any]:
    masked = {}
    for k, v in (data or {}).items():
        lk = str(k).lower()
        if lk in SENSITIVE_FIELDS:
            masked[k] = "***"
        else:
            masked[k] = v
    return masked


def setup_logging() -> None:
    """Configure structured logging"""

    log_level = getattr(logging, settings.server.log_level.upper())
    log_dir = "logs"
    app_log_file = os.path.join(log_dir, "prism_app.log")
    plugin_log_file = os.path.join(log_dir, "prism_plugins.log")

    # Ensure log directory exists
    os.makedirs(log_dir, exist_ok=True)

    # Get root logger and configure handlers
    root_logger = logging.getLogger()
    if root_logger.hasHandlers():
        root_logger.handlers.clear()
    root_logger.setLevel(log_level)

    formatter = logging.Formatter("%(message)s")

    # 1. Console handler (shows all logs)
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)
    root_logger.addHandler(console_handler)

    # 2. App file handler (for non-plugin logs)
    app_file_handler = handlers.RotatingFileHandler(
        app_log_file, maxBytes=10 * 1024 * 1024, backupCount=5, encoding="utf-8"
    )
    app_file_handler.setFormatter(formatter)
    app_file_handler.addFilter(AppLogFilter())
    root_logger.addHandler(app_file_handler)

    # 3. Plugin file handler (for plugin logs only)
    plugin_file_handler = handlers.RotatingFileHandler(
        plugin_log_file, maxBytes=5 * 1024 * 1024, backupCount=5, encoding="utf-8"
    )
    plugin_file_handler.setFormatter(formatter)
    plugin_file_handler.addFilter(PluginLogFilter())
    root_logger.addHandler(plugin_file_handler)

    # Configure structlog
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.StackInfoRenderer(),
            structlog.processors.CallsiteParameterAdder(
                parameters=[
                    CallsiteParameter.FILENAME,
                    CallsiteParameter.FUNC_NAME,
                    CallsiteParameter.LINENO,
                ]
            ),
            structlog.processors.TimeStamper(fmt="iso"),
            # 最小化字段 + 脱敏（开发环境不过滤，生产环境保留关键字段）
            (lambda logger, method, event_dict: event_dict) if settings.debug else (
                lambda logger, method, event_dict: {
                    **{k: v for k, v in event_dict.items() if k in {
                        "event", "timestamp", "log_level",
                        "request_id", "status_code", "duration_ms", "path", "method",
                        "plugin", "handler", "route", "error"
                    }},
                    **({"headers": _mask_sensitive(event_dict.get("headers", {}))} if event_dict.get("headers") else {}),
                }
            ),
            # 异常信息：生产输出结构化，开发控制台友好展示
            structlog.processors.dict_tracebacks if not settings.debug else structlog.processors.format_exc_info,
            ConsoleRenderer(exception_formatter=RichTracebackFormatter()) if settings.debug else structlog.processors.JSONRenderer(),
        ],
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    """Get a structured logger instance"""
    return structlog.get_logger(name)


class LoggerAdapter:
    """Adapter to add context to all log messages"""
    
    def __init__(self, logger: structlog.stdlib.BoundLogger, **context):
        self.logger = logger
        self.context = context
    
    def bind(self, **kwargs) -> "LoggerAdapter":
        """Add context variables"""
        new_context = {**self.context, **kwargs}
        return LoggerAdapter(self.logger, **new_context)
    
    def _log(self, method: str, event: str, **kwargs):
        """Internal log method"""
        kwargs.update(self.context)
        getattr(self.logger, method)(event, **kwargs)
    
    def debug(self, event: str, **kwargs):
        self._log("debug", event, **kwargs)
    
    def info(self, event: str, **kwargs):
        self._log("info", event, **kwargs)
    
    def warning(self, event: str, **kwargs):
        self._log("warning", event, **kwargs)
    
    def error(self, event: str, **kwargs):
        self._log("error", event, **kwargs)
    
    def critical(self, event: str, **kwargs):
        self._log("critical", event, **kwargs)


# Request logging middleware
async def log_request(request_id: str, method: str, path: str, **kwargs) -> Dict[str, Any]:
    """Log incoming request"""
    logger = get_logger("api.request")
    logger.info(
        "request_started",
        request_id=request_id,
        method=method,
        path=path,
        **kwargs
    )
    return {"request_id": request_id}


async def log_response(
    request_id: str,
    status_code: int,
    duration_ms: float,
    **kwargs
) -> None:
    """Log outgoing response"""
    logger = get_logger("api.response")
    logger.info(
        "request_completed",
        request_id=request_id,
        status_code=status_code,
        duration_ms=duration_ms,
        **kwargs
    ) 