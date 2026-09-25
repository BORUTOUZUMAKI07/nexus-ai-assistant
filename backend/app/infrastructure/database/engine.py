from backend.app.core.config import settings
from backend.app.core.logging import logger
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlalchemy.pool import NullPool
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

# NullPool is used because the DATABASE_URL points to Supabase's pgbouncer
# transaction-mode pooler (port 6543). In transaction mode each statement is
# executed on a different server connection, so SQLAlchemy's own connection pool
# adds no benefit and a persistent pool that pre-pings 20 remote connections on
# every request causes 5-12 s stalls over the internet round-trip to AWS
# ap-south-1. NullPool opens a fresh asyncpg connection per request (pgbouncer
# hands it one from its own pool), executes, and releases immediately.
engine: AsyncEngine = create_async_engine(
    settings.DATABASE_URL,
    echo=settings.DATABASE_ECHO,
    future=True,
    poolclass=NullPool,
    # Hosted Postgres (Supabase) uses pgbouncer transaction pooling, which can
    # conflict with asyncpg's prepared-statement cache (DuplicatePreparedStatementError).
    connect_args={"statement_cache_size": 0, "max_cached_statement_lifetime": 0},
)


async def init_db() -> None:
    """
    Initializes tables in database if they don't already exist.

    Dev-only convenience: creates missing tables so local bootstrapping just
    works. In production this is skipped — schema is owned by Alembic
    (backend/migrations/) and auto-creating here would silently mask migration
    drift between environments.
    """
    if settings.ENVIRONMENT == "production":
        logger.info("database_schema_managed_by_migrations")
        return
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
            await conn.execute(text("SELECT 1"))
        return True
    except Exception as e:
        logger.error("database_health_check_failed", error=str(e))
        return False
