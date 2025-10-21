"""
OpenTelemetry instrumentation middleware (optional, best-effort).
"""

from app.core.config import get_settings
from app.core.structured_logging import get_logger

settings = get_settings()
logger = get_logger("middleware.telemetry")


def setup_telemetry(app) -> None:
    """Setup OpenTelemetry instrumentation.
    - No-op if telemetry is disabled or dependencies are missing.
    """
    if not getattr(settings.monitoring, "telemetry_enabled", False):
        logger.info("Telemetry disabled")
        return

    try:
        # Lazy import to avoid hard dependency at import time
        from opentelemetry import trace
        from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
        # Optional instrumentations
        try:
            from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
        except Exception:
            FastAPIInstrumentor = None
        try:
            from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
        except Exception:
            HTTPXClientInstrumentor = None
        try:
            from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor
        except Exception:
            SQLAlchemyInstrumentor = None
        try:
            from opentelemetry.instrumentation.redis import RedisInstrumentor
        except Exception:
            RedisInstrumentor = None

        # Create resource/provider/exporter
        resource = Resource.create({
            "service.name": settings.app_name,
            "service.version": settings.app_version,
        })
        provider = TracerProvider(resource=resource)
        otlp_exporter = OTLPSpanExporter(
            endpoint=settings.monitoring.otlp_endpoint,
            insecure=True,
        )
        provider.add_span_processor(BatchSpanProcessor(otlp_exporter))
        trace.set_tracer_provider(provider)

        # Instrumentations (best-effort)
        if FastAPIInstrumentor:
            FastAPIInstrumentor.instrument_app(app)
        if HTTPXClientInstrumentor:
            HTTPXClientInstrumentor().instrument()
        if SQLAlchemyInstrumentor:
            try:
                from app.db.session import engine
                SQLAlchemyInstrumentor().instrument(
                    engine=engine.sync_engine,
                    service=f"{settings.app_name}-db",
                )
            except Exception:
                pass
        if RedisInstrumentor:
            try:
                RedisInstrumentor().instrument()
            except Exception:
                pass

        logger.info("Telemetry setup complete", endpoint=settings.monitoring.otlp_endpoint)
    except Exception as e:
        logger.info("Telemetry dependencies missing or failed to init; skipping", error=str(e))
        return