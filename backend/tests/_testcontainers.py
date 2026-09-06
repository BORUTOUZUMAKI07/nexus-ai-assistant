"""PostgreSQL testcontainer lifecycle and schema helpers for the test suite.

Replaces the old aiosqlite compatibility shim (``_sqlite_engine.py``) with a
real Dockerized Postgres mirror of production.  Domain models declare native
PostgreSQL column types (``JSONB``, ``ARRAY(Float)``, ``Text``, ``UUID``) that
previously needed compile-time monkeypatching for SQLite; on Postgres every
column, query, and constraint behaves exactly like the deployed database.

Containers are started lazily on first request and keyed by ``name`` so the
integration tier (``nexus_test`` database) and the live-server e2e tier
(``nexus_e2e`` database) stay isolated while sharing one image pull.
"""
from __future__ import annotations

import os
import threading

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlalchemy.orm import sessionmaker
from sqlmodel import SQLModel
from sqlmodel.ext.asyncio.session import AsyncSession

POSTGRES_IMAGE = os.environ.get("TEST_POSTGRES_IMAGE", "postgres:16-alpine")

_lock = threading.Lock()
_state: dict[str, tuple[object, str]] = {}


def _to_async_url(sync_url: str) -> str:
    return (
        sync_url.replace("postgresql+psycopg2://", "postgresql+asyncpg://")
        .replace("postgresql://", "postgresql+asyncpg://")
    )


def _to_psycopg2_dsn(sync_url: str) -> str:
    """Strip the SQLAlchemy driver suffix for direct psycopg2 use."""
    return sync_url.replace("postgresql+psycopg2://", "postgresql://")


def _create_database(sync_url: str, dbname: str) -> None:
    """Create ``dbname`` inside the container (idempotent)."""
    try:
        import psycopg2
    except ImportError:  # pragma: no cover
        return
    conn = psycopg2.connect(_to_psycopg2_dsn(sync_url))
    conn.autocommit = True
    try:
        with conn.cursor() as cur:
            cur.execute(f'CREATE DATABASE "{dbname}"')
    except psycopg2.errors.DuplicateDatabase:
        pass
    finally:
        conn.close()


def get_postgres_url(name: str = "nexus_test") -> str:
    """Lazily start a Postgres container (once per ``name``) and return its
    ``postgresql+asyncpg://`` URL.

    The first call sets ``DATABASE_URL`` so any component reading the
    environment late (langgraph checkpointer, late ``Settings()`` builds)
    observes the container instead of the localhost default.
    """
    with _lock:
        if name in _state:
            return _state[name][1]

        try:
            from testcontainers.community.postgres import (
                PostgresContainer,  # type: ignore[import-untyped]
            )
        except ImportError:  # pragma: no cover
            from testcontainers.postgres import (
                PostgresContainer,  # type: ignore[import-untyped]
            )

        pg = PostgresContainer(POSTGRES_IMAGE)
        pg.start()
        sync_url = pg.get_connection_url()
        if name != "nexus_test":
            _create_database(sync_url, name)
            async_url = _to_async_url(sync_url.rsplit("/", 1)[0] + "/" + name)
        else:
            async_url = _to_async_url(sync_url)

        os.environ["DATABASE_URL"] = async_url
        _state[name] = (pg, async_url)
        return async_url


def stop_containers() -> None:
    """Stop every container started this session."""
    global _state
    with _lock:
        for container, _url in _state.values():
            try:
                container.stop()
            except Exception:
                pass
        _state = {}


def build_test_engine(url: str) -> AsyncEngine:
    """Create an async engine against a testcontainer database."""
    return create_async_engine(
        url,
        future=True,
        echo=False,
        pool_pre_ping=True,
    )


def build_session_factory(engine: AsyncEngine):
    """Build a sqlmodel ``AsyncSession`` factory bound to ``engine``."""
    return sessionmaker(
        engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autocommit=False,
        autoflush=False,
    )


async def init_db_schema(engine: AsyncEngine) -> None:
    """Recreate every registered table on ``engine`` (fresh schema per run).

    ``drop_all`` first keeps runs deterministic even though the container
    persists between pytest invocations.
    """
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.drop_all)
        await conn.run_sync(SQLModel.metadata.create_all)


async def truncate_all(engine: AsyncEngine) -> None:
    """Empty every table (used between sessions / for hygiene)."""
    async with engine.begin() as conn:
        tables = [t.name for t in SQLModel.metadata.sorted_tables]
        if tables:
            await conn.execute(text(f'TRUNCATE TABLE {", ".join(tables)} RESTART IDENTITY CASCADE'))
