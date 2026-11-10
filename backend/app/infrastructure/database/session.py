from collections.abc import AsyncGenerator

from backend.app.infrastructure.database.engine import engine, get_read_engine
from sqlalchemy.orm import sessionmaker
from sqlmodel.ext.asyncio.session import AsyncSession

# Async session factory for the write (primary) engine.
async_session_factory = sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autocommit=False,
    autoflush=False,
)


async def get_db_session() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI yield dependency providing an isolated AsyncSession per request."""
    async with async_session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


async def get_read_db_session() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI yield dependency for read-only queries.

    Routes to the read-replica engine when DATABASE_READ_URL is configured,
    otherwise falls back to the write engine. Intended for read-heavy paths
    (file listings, chunk reads, usage lookups) so read traffic does not
    contend with writes on the primary.
    """
    read_engine = get_read_engine()
    read_factory = sessionmaker(
        read_engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autocommit=False,
        autoflush=False,
    )
    async with read_factory() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()
