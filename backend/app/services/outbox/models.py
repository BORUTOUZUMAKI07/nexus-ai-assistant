"""The durable queue of work that must happen after an answer is committed.

One row is one idempotent job: cache-bucket invalidation or a webhook delivery.
Index names are the exact ``ix_<table>_<column>`` form SQLModel derives from
``index=True`` -- Alembic autogenerate reports permanent drift for any other
name, and ``f9a0b1c2d3e4`` creates the same five indexes by hand.

Timestamps are naive UTC, matching the rest of the schema's
``TIMESTAMP WITHOUT TIME ZONE`` columns and the migration's
``(now() AT TIME ZONE 'utc')`` defaults. RLS is applied by the
``ddl_command_end`` event trigger installed in ``a7b8c9d0e1f2``; do not repeat
those statements here.
"""
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import Column, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, SQLModel


def _utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class PostAnswerTask(SQLModel, table=True):
    __tablename__ = "post_answer_tasks"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_post_answer_tasks_idempotency"),
        {"extend_existing": True},
    )

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True, index=True)
    # ON DELETE CASCADE: a deleted run must not leave undeliverable work behind.
    run_id: uuid.UUID = Field(
        foreign_key="agent_runs.id", ondelete="CASCADE", index=True, nullable=False
    )
    message_id: uuid.UUID | None = Field(default=None, index=True)
    task_type: str = Field(nullable=False)
    # Shaped as "<run_id>:<message_id>:<task_type>" by the service, so a retried
    # persistence cannot enqueue the same job twice.
    idempotency_key: str = Field(nullable=False)
    payload: dict[str, Any] = Field(
        default_factory=dict,
        sa_column=Column(
            JSONB, nullable=False, server_default=text("'{}'::jsonb")
        ),
    )
    # pending -> running -> succeeded | failed -> dead_letter (failed is a
    # transient marker on the way back to pending or into dead_letter).
    status: str = Field(default="pending", index=True, nullable=False)
    attempts: int = Field(default=0, nullable=False)
    max_attempts: int = Field(default=5, nullable=False)
    available_at: datetime = Field(default_factory=_utcnow, index=True, nullable=False)
    locked_at: datetime | None = Field(default=None)
    last_error: str | None = Field(default=None)
    created_at: datetime = Field(default_factory=_utcnow, nullable=False)
    finished_at: datetime | None = Field(default=None)
