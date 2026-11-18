"""durable post-answer outbox

Revision ID: f9a0b1c2d3e4
Revises: e7f8a9b0c1d2
Create Date: 2026-11-18

The chat run used to perform two pieces of post-answer bookkeeping inline,
after the assistant message, usage row and cost row had already been committed:
invalidating the semantic response cache and dispatching `message.completed`
to registered webhooks. A slow or retrying endpoint therefore sat directly on
the `done` path even though the answer was already durable.

This revision adds the durable queue those two jobs move to. Rows are created
with `ON CONFLICT DO NOTHING` on `idempotency_key`, shaped from
(run_id, message_id, task_type), so a retried or duplicated persistence cannot
queue the same work twice. The worker claims with `FOR UPDATE SKIP LOCKED` and
applies bounded exponential backoff before a row reaches `dead_letter`.

Row-level security is deliberately not written here. `a7b8c9d0e1f2` installed a
`ddl_command_end` event trigger that enables RLS and revokes Supabase's default
grants on every table created after it, so repeating that policy by hand would
be a second source of truth for the same fact.

Index names are the exact `ix_<table>_<column>` form SQLModel derives from
`index=True`; autogenerate reports permanent drift for any other name.

downgrade(): intentional NO-OP (forward-only). Never drops queued work.
"""
from typing import Sequence, Union

from alembic import op

revision: str = "f9a0b1c2d3e4"
down_revision: Union[str, None] = "e7f8a9b0c1d2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Exact name declared in the SQLModel ``__table_args__``. Kept as a named
# constant (rather than buried in the DDL) so it cannot drift from the model
# and the insert's ON CONFLICT target without a test noticing.
_IDEMPOTENCY_UQ = "uq_post_answer_tasks_idempotency"


def upgrade() -> None:
    # Each statement goes out alone: asyncpg rejects a multi-statement string
    # with "cannot insert multiple commands into a prepared statement".
    op.execute(f"""
        CREATE TABLE IF NOT EXISTS post_answer_tasks (
            id UUID PRIMARY KEY,
            run_id UUID NOT NULL REFERENCES agent_runs(id) ON DELETE CASCADE,
            message_id UUID,
            task_type VARCHAR NOT NULL,
            idempotency_key VARCHAR NOT NULL,
            payload JSONB NOT NULL DEFAULT '{{}}'::jsonb,
            status VARCHAR NOT NULL DEFAULT 'pending',
            attempts INTEGER NOT NULL DEFAULT 0,
            max_attempts INTEGER NOT NULL DEFAULT 5,
            available_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT (now() AT TIME ZONE 'utc'),
            locked_at TIMESTAMP WITHOUT TIME ZONE,
            last_error TEXT,
            created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT (now() AT TIME ZONE 'utc'),
            finished_at TIMESTAMP WITHOUT TIME ZONE,
            CONSTRAINT {_IDEMPOTENCY_UQ} UNIQUE (idempotency_key)
        )
    """)
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_post_answer_tasks_id ON post_answer_tasks (id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_post_answer_tasks_run_id ON post_answer_tasks (run_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_post_answer_tasks_status ON post_answer_tasks (status)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_post_answer_tasks_available_at "
        "ON post_answer_tasks (available_at)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_post_answer_tasks_message_id "
        "ON post_answer_tasks (message_id)"
    )


def downgrade() -> None:
    """No-op by design: forward-only. Never drops queued work.

    A downgrade would have to drop the table, which would destroy work that has
    not been delivered yet. Recovery is a forward migration, not a rollback.
    """
    pass
