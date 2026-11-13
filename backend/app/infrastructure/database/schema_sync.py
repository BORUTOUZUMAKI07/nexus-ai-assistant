"""Additive schema repair for databases that fell behind the models.

Why this exists
───────────────
Production does not run ``create_all`` (``init_db`` returns early when
ENVIRONMENT=production), and the deployed database had no migration runner of
its own, so its schema was frozen at whatever the last manual
``alembic upgrade head`` produced. The models kept gaining columns --
``messages.cost_usd`` among them -- and nothing ever added them to the live
database. The result was that every request which selected a message returned
500 (conversation detail, plans, chat) while endpoints that never touch
``messages`` kept working.

The migration ``z1a2b3c4d5e6`` repairs the same drift, but it can only help if
something actually runs migrations against the deployment. This module is the
belt to that braces: it runs at start-up, so a database that is behind is
corrected the next time the service boots, whatever the deployment pipeline
does.

It only ever ADDs a column. It never drops, renames, retypes, or narrows
anything, and it skips a NOT NULL column that has no default because a
populated table cannot accept one without a backfill. That makes it safe to run
on every boot: when there is nothing to do it issues no DDL at all.
"""
from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncEngine
from sqlalchemy.schema import CreateColumn
from sqlmodel import SQLModel

from app.core.logging import logger

__all__ = ["reconcile_missing_columns"]


def _import_all_models() -> None:
    """Bind every table to ``SQLModel.metadata``.

    Metadata is empty until the model modules are imported, and an empty
    metadata would make this a silent no-op -- the exact failure it prevents.
    Importing the API routers pulls in the models transitively, which is how a
    booted application already ends up with a full metadata object.
    """
    import importlib
    from pathlib import Path

    import app.domain as domain_pkg

    # Walk the directory instead of `pkgutil.iter_modules`: these are implicit
    # namespace packages (no `__init__.py`), and `iter_modules` does not list
    # namespace portions -- it returned just one module here, leaving the
    # metadata nearly empty and the whole reconcile a silent no-op.
    for entry in sorted(Path(domain_pkg.__path__[0]).iterdir()):
        if not entry.is_dir() or not (entry / "models.py").exists():
            continue
        try:
            importlib.import_module(f"app.domain.{entry.name}.models")
        except ModuleNotFoundError:
            continue


def _reconcile_sync(connection: sa.Connection) -> list[str]:
    inspector = sa.inspect(connection)
    existing_tables = set(inspector.get_table_names())
    added: list[str] = []

    for table in SQLModel.metadata.sorted_tables:
        if table.name not in existing_tables:
            continue
        present = {column["name"] for column in inspector.get_columns(table.name)}
        for column in table.columns:
            if column.name in present:
                continue
            if (
                not column.nullable
                and column.default is None
                and column.server_default is None
            ):
                continue
            ddl = CreateColumn(column).compile(dialect=connection.dialect)
            connection.execute(sa.text(f'ALTER TABLE "{table.name}" ADD COLUMN {ddl}'))
            added.append(f"{table.name}.{column.name}")

    return added


async def reconcile_missing_columns(engine: AsyncEngine) -> list[str]:
    """Add every model column the database is missing. Returns what was added."""
    _import_all_models()
    try:
        async with engine.begin() as connection:
            added = await connection.run_sync(_reconcile_sync)
    except Exception as exc:  # pragma: no cover - depends on the target database
        # Start-up must not fail because the repair could not run: serving with
        # a known-missing column is recoverable (the 503 names it), refusing to
        # boot is not.
        logger.error(
            "database_schema_reconcile_failed", error_type=type(exc).__name__
        )
        return []

    if added:
        logger.warning(
            "database_schema_reconciled",
            added_columns=added,
            column_count=len(added),
        )
    return added
