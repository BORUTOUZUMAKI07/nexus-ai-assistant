from backend.app.core.config import settings
from backend.app.core.logging import logger
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlmodel import SQLModel

# Import all domain models to ensure tables are registered in SQLModel.metadata
try:
    import backend.app.domain.conversation.models  # noqa: F401
    import backend.app.domain.file.models  # noqa: F401
    import backend.app.domain.prompt.models  # noqa: F401
    import backend.app.domain.system.models  # noqa: F401
    import backend.app.domain.tool.models  # noqa: F401
    import backend.app.domain.usage.models  # noqa: F401
    import backend.app.domain.user.models  # noqa: F401
except Exception as exc:
    logger.warning("domain_models_import_warning", error=str(exc))

# Create the global AsyncEngine for PostgreSQL / Supabase
engine: AsyncEngine = create_async_engine(
    settings.DATABASE_URL,
    echo=settings.ENVIRONMENT == "development",
    future=True,
    pool_size=20,
    max_overflow=10,
    pool_pre_ping=True,
    pool_recycle=3600,
)


async def init_db() -> None:
    """Initializes tables in database if they don't already exist."""
    try:
        async with engine.begin() as conn:
            await conn.run_sync(SQLModel.metadata.create_all)
        logger.info("database_schema_synced")
    except Exception as exc:
        logger.error("database_schema_sync_failed", error=str(exc))
        raise


async def close_db() -> None:
    """Disposes of engine connections upon application shutdown."""
    await engine.dispose()
    logger.info("database_engine_disposed")


async def check_database_health() -> bool:
    """Performs a lightweight pre-ping select query to verify database health."""
    try:
        async with engine.connect() as conn:
            await conn.execute("SELECT 1")
        return True
    except Exception as e:
        logger.error("database_health_check_failed", error=str(e))
        return False
