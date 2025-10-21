"""
Database session management using SQLAlchemy with asyncpg.
"""

from typing import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
from sqlalchemy.orm import declarative_base

from app.core.config import get_settings
from app.core.structured_logging import get_logger
import orjson

logger = get_logger("app.db.session")
settings = get_settings()

# Create async engine
engine = create_async_engine(
    settings.database.url,
    pool_pre_ping=True,
    json_serializer=orjson.dumps,
    json_deserializer=orjson.loads,
)

# Create async session factory
AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """
    Dependency to get database session.
    Usage:
        @app.get("/items")
        async def get_items(db: AsyncSession = Depends(get_db)):
            ...
    """
    session = AsyncSessionLocal()
    try:
        yield session
    except Exception:
        await session.rollback()
        raise
    finally:
        await session.close()


async def init_db():
    """Initialize the database."""
    async with engine.begin() as conn:
        from app.db.models import Base
        # This will create all tables defined in models that inherit from Base
        await conn.run_sync(Base.metadata.create_all)
    logger.info("Database tables created/verified.")


async def close_db():
    """Close the database connection."""
    await engine.dispose() 