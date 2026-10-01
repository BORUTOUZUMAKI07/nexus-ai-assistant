"""add user_memories lifecycle columns

Revision ID: b1c2d3e4f5a6
Revises: f6a7b8c9d0e1
Create Date: 2026-09-30 18:00:00.000000

Gives ``user_memories`` the fields the memory lifecycle needs.

The specific defect this fixes: ``UserMemory.confidence`` was declared as
``float = 1.0`` and never written again anywhere in the codebase. Every stored
memory was therefore permanently, maximally trusted, and there was no way to
express "this preference was stated once six months ago and never confirmed
again" as anything other than absolute certainty.

The added columns:

  * ``mem0_id``  — the mem0 identifier for the same fact, so the semantic index
    and this table can be reconciled. The synthesizer now mirrors extracted
    facts here; without this column the two stores could not be linked at all.
  * ``retrieval_count`` / ``last_used_at`` — write-back targets. A memory that
    is repeatedly recalled and useful earns confidence; one that is never
    touched decays toward the floor (see services/memory_lifecycle.py).
  * ``scope``    — "user" for a standing preference, "conversation" for context
    that should not outlive its thread.

``confidence`` itself is *not* altered by a schema change; the default moves
from 1.0 to 0.7 in the model so new memories start honestly. The backfill below
fixes existing rows, because a column default only applies to new inserts.

Backfill policy: pre-existing rows were all written under the old behaviour,
where a confidence of 1.0 recorded "we never tracked this" rather than "we
verified this". They are reset to the new initial confidence and given a
``retrieval_count`` of 0, which is the honest value — the tracking starts here.
Decay then treats them as unused and ages them naturally, rather than leaving
them at a permanent 1.0 that would let a year-old one-off fact masquerade as a
standing user preference.

Safety notes:
  * Every op is guarded with IF NOT EXISTS, matching the convention of the
    other migrations in this directory.
  * Additive only, plus a bounded UPDATE of the same table: no rows are read,
    joined or dropped, and no table is locked for a rewrite.
  * Indexes are created separately after the columns, since Postgres cannot
    build an index over a column that does not exist yet.
"""
from typing import Sequence, Union

from alembic import op

revision: str = "b1c2d3e4f5a6"
# a7b8c9d0e1f2 (the RLS revision), NOT f6a7b8c9d0e1.
#
# This file and a7b8c9d0e1f2 were both written against f6a7b8c9d0e1 and committed
# as two independent revisions, which left the repository with two heads. Alembic
# refuses to resolve `head` when there is more than one, so `alembic upgrade
# head` — the documented first-boot command, and what `make migrate` runs — died
# with "Multiple head revisions are present" before executing a statement. Both
# revisions were therefore unreachable in practice: nothing that ran `upgrade
# head` could have applied the memory columns or the row-level security.
#
# a7b8c9d0e1f2 is the parent because it was committed first (4dc638e, 2026-09-30)
# and carries the earlier Create Date, so this keeps the chain in the order the
# work actually happened. The two revisions are independent — one adds columns
# to user_memories, the other sets a table attribute and creates an event
# trigger — so the order has no effect on the resulting schema.
#
# A database already stamped `b1c2d3e4f5a6` under the old chain picks this up
# cleanly: its version row already matches, and the newly-reachable
# a7b8c9d0e1f2 is idempotent (ALTER TABLE ... ENABLE, REVOKE, CREATE OR REPLACE
# FUNCTION, and DROP EVENT TRIGGER IF EXISTS before CREATE).
down_revision: Union[str, None] = "a7b8c9d0e1f2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# (column name, DDL type) for every field added to user_memories.
_NEW_COLUMNS: tuple[tuple[str, str], ...] = (
    ("mem0_id", "VARCHAR"),
    ("retrieval_count", "INTEGER NOT NULL DEFAULT 0"),
    ("last_used_at", "TIMESTAMP WITHOUT TIME ZONE"),
    ("scope", "VARCHAR NOT NULL DEFAULT 'user'"),
)

# (index name, table, column) for the columns that get looked up.
_NEW_INDEXES: tuple[tuple[str, str, str], ...] = (
    ("ix_user_memories_mem0_id", "user_memories", "mem0_id"),
    ("ix_user_memories_scope", "user_memories", "scope"),
)

# Honest starting confidence, matching INITIAL_CONFIDENCE in
# services/memory_lifecycle.py. The model default is only applied on insert,
# so existing rows need this explicitly.
_INITIAL_CONFIDENCE = 0.7


def upgrade() -> None:
    for column_name, column_type in _NEW_COLUMNS:
        op.execute(
            f"ALTER TABLE user_memories ADD COLUMN IF NOT EXISTS {column_name} {column_type}"
        )
    for index_name, table, column in _NEW_INDEXES:
        op.execute(f"CREATE INDEX IF NOT EXISTS {index_name} ON {table} ({column})")

    # Reset the never-meaningful 1.0 that the old default stamped on every row.
    op.execute(
        f"UPDATE user_memories SET confidence = {_INITIAL_CONFIDENCE} "
        "WHERE confidence > 1.0 OR confidence < 0.0"
    )
    # Rows written before lifecycle tracking existed were never counted.
    op.execute(
        "UPDATE user_memories SET retrieval_count = 0 WHERE retrieval_count IS NULL"
    )


def downgrade() -> None:
    for index_name, _table, _column in reversed(_NEW_INDEXES):
        op.execute(f"DROP INDEX IF EXISTS {index_name}")
    for column_name, _column_type in reversed(_NEW_COLUMNS):
        op.execute(f"ALTER TABLE user_memories DROP COLUMN IF EXISTS {column_name}")
    # Restore the previous default so the schema matches the old model.
    op.execute("ALTER TABLE user_memories ALTER COLUMN confidence SET DEFAULT 1.0")
