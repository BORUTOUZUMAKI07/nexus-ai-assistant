"""
Run Domain Model — the durable record of one agent run.

The defect this exists to fix: the SSE endpoint ran the LangGraph **inside the
`StreamingResponse` async generator**, so a run's lifetime was the HTTP
request's. A refresh, a tab close, or a proxy timeout ended the run. The
assistant message was only written when the generator reached the end of its
loop, on a session opened *after* the fact, so a disconnect yielded at best the
partial text already streamed and then nothing: no completion, no usage row, no
cost row, and a permanently half-answered conversation.

Two tables, with a deliberately narrow split:

  * ``AgentRun``  — one row per run. ``status`` is the **single** source of
    truth for whether the run is over. Nothing infers termination from the
    event stream, because a stream that ends is also what a dropped client
    looks like.
  * ``RunEvent``  — the ordered frames, so a client that reconnects can be
    handed exactly what it missed. ``seq`` is per-run and gap-free.

There is deliberately **no** ``event_type`` column. The frame's type lives in
``payload["type"]``; a second column holding the same value would be a second
source of truth for it (the same trap as ``RAG_ANSWER_COVERAGE_DROP_BELOW``,
AGENTS.md §9.18), and nothing needs to query by type — replay is by ``seq`` and
termination is ``AgentRun.status``.

``message_id`` is set when the assistant reply is persisted, which now happens
in the executor regardless of whether any client is still listening.
"""
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import Column, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, SQLModel


class AgentRun(SQLModel, table=True):
    __tablename__ = "agent_runs"
    __table_args__ = {"extend_existing": True}

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True, index=True)
    # Indexed because the rejoin endpoint looks a run up by conversation as well
    # as by id, and because the "is anything still running for this thread"
    # question is per-conversation.
    conversation_id: uuid.UUID = Field(foreign_key="conversations.id", index=True, nullable=False)
    # Carried here rather than joined through the conversation so the IDOR check
    # never depends on the conversation row being readable.
    user_id: uuid.UUID = Field(foreign_key="users.id", index=True, nullable=False)
    thread_id: str = Field(nullable=False, description="LangGraph thread id; equals conversation_id as text")
    mode: str = Field(default="normal", description="normal | arq | fast")
    # running | completed | failed
    status: str = Field(default="running", index=True, description="running | completed | failed")
    error: str | None = Field(default=None, description="Failure detail when status is failed")
    # The assistant reply this run produced. Set on completion, and the reason a
    # disconnected client's work is not lost.
    message_id: uuid.UUID | None = Field(default=None, description="Assistant message id, set on completion")
    # The frame count at the time the run finished. Diagnostics only — replay
    # never trusts it, because the authoritative "is there more" test is
    # reading the rows themselves.
    event_count: int = Field(default=0)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC).replace(tzinfo=None))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC).replace(tzinfo=None))
    finished_at: datetime | None = Field(default=None)


class RunEvent(SQLModel, table=True):
    __tablename__ = "run_events"
    __table_args__ = (
        UniqueConstraint("run_id", "seq", name="uq_run_events_run_id_seq"),
        {"extend_existing": True},
    )

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True, index=True)
    run_id: uuid.UUID = Field(foreign_key="agent_runs.id", index=True, nullable=False)
    # 1-based, gap-free, assigned when the frame is *created* rather than when
    # it is written. That ordering is what lets the live broker path deliver a
    # frame immediately while the database copy is still batched behind it:
    # broker order, disk order and seq order are the same order.
    seq: int = Field(nullable=False, index=True)
    # The whole frame as sent to the client, including its "type". Replay
    # re-serialises this verbatim, so a joined client sees the identical frame
    # the original client did.
    payload: dict[str, Any] = Field(
        default_factory=dict, sa_column=Column(JSONB, nullable=False)
    )
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC).replace(tzinfo=None))
