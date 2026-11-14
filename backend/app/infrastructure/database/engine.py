from backend.app.core.config import settings
from backend.app.core.logging import logger
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlalchemy.pool import NullPool
from sqlmodel import SQLModel

# Import all domain models to ensure tables are registered in SQLModel.metadata
try:
    import backend.app.domain.artifact.models  # noqa: F401
    import backend.app.domain.conversation.models  # noqa: F401
    import backend.app.domain.experiment.models  # noqa: F401
    import backend.app.domain.file.models  # noqa: F401
    import backend.app.domain.hook.models  # noqa: F401
    import backend.app.domain.optimization.models  # noqa: F401
    import backend.app.domain.org.models  # noqa: F401
    import backend.app.domain.plan.models  # noqa: F401
    import backend.app.domain.prompt.models  # noqa: F401
    import backend.app.domain.redteam.models  # noqa: F401
    import backend.app.domain.run.models  # noqa: F401
    import backend.app.domain.share.models  # noqa: F401
    import backend.app.domain.system.models  # noqa: F401
    import backend.app.domain.tool.models  # noqa: F401
    import backend.app.domain.usage.models  # noqa: F401
    import backend.app.domain.user.models  # noqa: F401
    import backend.app.domain.webhook.models  # noqa: F401
except Exception as exc:
    logger.warning("domain_models_import_warning", error_type=type(exc).__name__)

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

# Read-replica engine, lazily created only when DATABASE_READ_URL is set.
# When unset, all reads fall back to the write engine (single-engine behaviour).
_read_engine: AsyncEngine | None = None


def get_read_engine() -> AsyncEngine:
    """Return the read-replica engine, or the write engine when no replica is configured.

    Created lazily so a deployment without a read replica pays no engine cost.
    """
    global _read_engine
    if _read_engine is not None:
        return _read_engine
    if settings.DATABASE_READ_URL:
        _read_engine = create_async_engine(
            settings.DATABASE_READ_URL,
            echo=settings.DATABASE_ECHO,
            future=True,
            poolclass=NullPool,
            connect_args={"statement_cache_size": 0, "max_cached_statement_lifetime": 0},
        )
        logger.info("database_read_engine_created")
    else:
        _read_engine = engine
    return _read_engine


def get_write_engine() -> AsyncEngine:
    """Return the write (primary) engine."""
    return engine


async def init_db() -> None:
    """Bring the schema up to what the models need, without ever destroying data.

    Two different jobs, historically conflated:

    * Creating missing *tables* is a dev convenience (``create_all``) and is
      skipped in production, where Alembic owns the schema.
    * Adding missing *columns* is a repair, and it belongs in every
      environment. ``create_all`` never alters an existing table, so a column
      added to a model after its table was first created is silently absent --
      which is exactly how the deployment ended up serving
      ``column messages.cost_usd does not exist`` on every conversation read
      while the list endpoint (which never selects ``messages``) kept working.

    The repair is additive and idempotent (see ``schema_sync``), so running it
    unconditionally costs one catalog read per boot when nothing is missing.
    """
    from app.infrastructure.database.schema_sync import reconcile_missing_columns

    if settings.ENVIRONMENT != "production":
        try:
            async with engine.begin() as conn:
                await conn.run_sync(SQLModel.metadata.create_all)
            logger.info("database_schema_synced")
        except Exception as exc:
            logger.error("database_schema_sync_failed", error_type=type(exc).__name__)
            raise
    else:
        logger.info("database_schema_managed_by_migrations")

    # Every environment: an older schema must not turn reads into 500s. This
    # also covers a deployment that is not actually running with
    # ENVIRONMENT=production -- a first version of this repair lived inside the
    # production branch only, and on the deployed service it never ran at all.
    await reconcile_missing_columns(engine)


async def close_db() -> None:
    """Disposes of engine connections upon application shutdown."""
    await engine.dispose()
    if _read_engine is not None and _read_engine is not engine:
        await _read_engine.dispose()
    logger.info("database_engine_disposed")


async def check_database_health() -> bool:
    """Performs a lightweight pre-ping select query to verify database health."""
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        return True
    except Exception as e:
        logger.error("database_health_check_failed", error_type=type(e).__name__)
        return False
