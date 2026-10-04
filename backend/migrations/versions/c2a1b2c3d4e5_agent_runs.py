"""add agent_runs and run_events

Revision ID: c2a1b2c3d4e5
Revises: b1c2d3e4f5a6
Create Date: 2026-10-04 12:00:00.000000

Gives an agent run a durable identity and an ordered event log.

The defect this fixes: the SSE endpoint executed the LangGraph **inside its own
``StreamingResponse`` async generator**, so a run's lifetime was the HTTP
request's. A refresh, a tab close or a proxy timeout ended the generator, the
event loop hit its ``request.is_disconnected()`` break, and the rest of the answer
was never produced — along with the assistant message, the usage row and the cost
row, because those were written *after* that break. The user was left with a
half-answered conversation that looked finished.

Reconnecting could not be fixed from the browser, because there was nothing to
reconnect to: no run id, no event log, and no endpoint that could replay frames.
These two tables are that missing state.

``agent_runs``
    One row per run. ``status`` is the single source of truth for whether the run
    is over — deliberately not derived from the event stream, because a stream
    that stops is indistinguishable from a client that vanished, which is the
    failure being removed.

``run_events``
    The frames in order. ``uq_run_events_run_id_seq`` is what makes ``seq``
    trustworthy: sequence numbers are assigned when a frame is created, so two
    concurrent writers for one run cannot silently interleave a duplicated
    sequence, and a reader can treat "the next seq" as the next frame rather than
    hoping rows arrive contiguously.

There is deliberately no ``event_type`` column. The frame's type lives in
``payload["type"]``, and a second column holding the same value would be a second
source of truth for it — nothing needs to query by type, since replay is by
``seq`` and termination is ``agent_runs.status``.

Row-level security is not enabled here. ``a7b8c9d0e1f2`` installed a
``ddl_command_end`` event trigger that covers tables created after it, and this
file runs after it, so both tables are protected by the same mechanism as the
other 35 rather than by a hand-repeated copy of it.

Index names are the exact ``ix_<table>_<column>`` form SQLModel generates from
``index=True``, because autogenerate reports permanent drift against any other
name.
"""
from typing import Sequence, Union

from alembic import op

revision: str = "c2a1b2c3d4e5"
down_revision: Union[str, None] = "b1c2d3e4f5a6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_RUNS_TABLE = "agent_runs"
_EVENTS_TABLE = "run_events"
# Matches the name declared in domain/run/models.py. Not optional: an unnamed
# constraint gets a database-generated name, and autogenerate would then report
# the constraint as missing forever.
_SEQ_CONSTRAINT = "uq_run_events_run_id_seq"


def upgrade() -> None:
    op.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {_RUNS_TABLE} (
            id UUID PRIMARY KEY,
            conversation_id UUID NOT NULL REFERENCES conversations(id),
            user_id UUID NOT NULL REFERENCES users(id),
            thread_id VARCHAR NOT NULL,
            mode VARCHAR NOT NULL DEFAULT 'normal',
            status VARCHAR NOT NULL DEFAULT 'running',
            error TEXT,
            message_id UUID,
            event_count INTEGER NOT NULL DEFAULT 0,
            created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT (now() AT TIME ZONE 'utc'),
            updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT (now() AT TIME ZONE 'utc'),
            finished_at TIMESTAMP WITHOUT TIME ZONE
        )
        """
    )
    # Created separately from the table because Postgres cannot build an index
    # over a column that does not exist yet.
    # Index names are written out rather than built from a format string. They are
    # the names SQLModel derives from ``index=True`` and the ones autogenerate
    # compares against, so a migration that assembles them at runtime is a
    # migration whose names no reader -- human or tool -- can see.
    for index_name, column in (
        ("ix_agent_runs_id", "id"),
        ("ix_agent_runs_conversation_id", "conversation_id"),
        ("ix_agent_runs_user_id", "user_id"),
        ("ix_agent_runs_status", "status"),
    ):
        op.execute(
            f"CREATE INDEX IF NOT EXISTS {index_name} ON {_RUNS_TABLE} ({column})"
        )

    op.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {_EVENTS_TABLE} (
            id UUID PRIMARY KEY,
            run_id UUID NOT NULL REFERENCES {_RUNS_TABLE}(id) ON DELETE CASCADE,
            seq INTEGER NOT NULL,
            payload JSONB NOT NULL,
            created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT (now() AT TIME ZONE 'utc'),
            CONSTRAINT {_SEQ_CONSTRAINT} UNIQUE (run_id, seq)
        )
        """
    )
    for index_name, column in (
        ("ix_run_events_id", "id"),
        ("ix_run_events_run_id", "run_id"),
        ("ix_run_events_seq", "seq"),
    ):
        op.execute(
            f"CREATE INDEX IF NOT EXISTS {index_name} ON {_EVENTS_TABLE} ({column})"
        )


def downgrade() -> None:
    # Events first: they reference runs.
    op.execute(f"DROP TABLE IF EXISTS {_EVENTS_TABLE}")
    op.execute(f"DROP TABLE IF EXISTS {_RUNS_TABLE}")
