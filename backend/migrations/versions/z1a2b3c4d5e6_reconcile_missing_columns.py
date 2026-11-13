"""reconcile columns the models have and the database does not

Revision ID: z1a2b3c4d5e6
Revises: 4289b5262252
Create Date: 2026-11-13

Why this exists
───────────────
The migration history does not cover every column the models declare, so a
database built by an older `alembic upgrade head` is missing columns that were
added to a model afterwards and never got an ALTER. The deployed database was
in exactly that state: `GET /conversations/{id}`, `/plans` and `/chat` all
failed with `column messages.cost_usd does not exist` while the conversation
list worked, because listing does not select `messages`.

`0001_initial_schema` builds its schema with `SQLModel.metadata.create_all`, so
on a *fresh* database every column exists and nothing looks wrong locally; the
drift only appears on a database that was migrated earlier and left behind.
Nothing in the history adds `messages.cost_usd`, so no amount of applying the
existing chain fixes it.

This revision closes that gap generically: it compares the database to the
model metadata and adds whatever columns are missing. It only ever ADDs --
never drops, renames, or changes a type -- so it is safe to run against a
populated database, and it is idempotent: a second run finds nothing to do.
Columns that are NOT NULL without a default are skipped, because they cannot be
added to a table that already has rows; those need a hand-written backfill.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.schema import CreateColumn
from sqlmodel import SQLModel

revision: str = "z1a2b3c4d5e6"
down_revision: Union[str, None] = "4289b5262252"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _import_all_models() -> None:
    """Bind every table to ``SQLModel.metadata`` before comparing it.

    A migration cannot rely on the application having imported its models: the
    metadata is empty until they are, and an empty metadata would make this
    revision a silent no-op -- the exact failure mode it exists to prevent.
    """
    import importlib
    from pathlib import Path

    import backend.app.domain as domain_pkg

    # Filesystem walk, not `pkgutil.iter_modules`: the domain packages are
    # implicit namespace packages, so iter_modules does not list them.
    for entry in sorted(Path(domain_pkg.__path__[0]).iterdir()):
        if not entry.is_dir() or not (entry / "models.py").exists():
            continue
        try:
            importlib.import_module(f"backend.app.domain.{entry.name}.models")
        except ModuleNotFoundError:
            # A domain package without a models module (e.g. a pure schema
            # package) simply contributes no tables.
            continue


def upgrade() -> None:
    _import_all_models()
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_tables = set(inspector.get_table_names())
    added: list[str] = []

    for table in SQLModel.metadata.sorted_tables:
        if table.name not in existing_tables:
            # create_all in 0001 owns table creation; adding a column to a
            # table this database has never had is not this revision's job.
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
                # Cannot satisfy existing rows; leave it to a written backfill.
                continue
            ddl = CreateColumn(column).compile(dialect=bind.dialect)
            bind.execute(
                sa.text(f'ALTER TABLE "{table.name}" ADD COLUMN {ddl}')
            )
            added.append(f"{table.name}.{column.name}")

    if added:
        print(f"reconcile_missing_columns: added {len(added)} column(s): {', '.join(added)}")


def downgrade() -> None:
    """No-op by design.

    The columns this adds are the ones the models require. Dropping them again
    would put the database back into the state that broke every conversation
    read, and which specific columns were added depends on how far behind the
    database was, so there is no single correct inverse.
    """
    pass
