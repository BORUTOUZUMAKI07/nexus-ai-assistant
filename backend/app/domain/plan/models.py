"""
Plan Domain Models.

A ``Plan`` is the plan-then-approve contract: the assistant drafts a plan
(title + summary + ordered steps) against a conversation, the owner approves
or rejects it, and only an approved plan is used to steer tool/agent execution.
The model is intentionally small — the plan generation/approval use cases live
in ``backend/app/services/plan_service.py``.
"""
import uuid
from datetime import UTC, datetime

from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Column, Field, SQLModel


class Plan(SQLModel, table=True):
    __tablename__ = "plans"
    __table_args__ = {"extend_existing": True}

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True, index=True)
    conversation_id: uuid.UUID = Field(
        foreign_key="conversations.id", index=True, nullable=False
    )
    user_id: uuid.UUID = Field(foreign_key="users.id", index=True, nullable=False)
    title: str = Field(default="Plan", nullable=False)
    summary: str | None = Field(default=None, description="One-paragraph plan rationale")
    steps: list[str] = Field(default_factory=list, sa_column=Column(JSONB, nullable=False))
    status: str = Field(default="pending", description="pending | approved | rejected")
    decision_reason: str | None = Field(
        default=None, description="Rejection reason, or any human note on the decision"
    )
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC).replace(tzinfo=None))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC).replace(tzinfo=None))
    decided_at: datetime | None = Field(
        default=None, description="When the human approved/rejected the plan"
    )
