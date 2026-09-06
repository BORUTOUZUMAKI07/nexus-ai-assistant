import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Column, Field, SQLModel


class Conversation(SQLModel, table=True):
    __tablename__ = "conversations"
    __table_args__ = {"extend_existing": True}

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True, index=True)
    user_id: uuid.UUID = Field(foreign_key="users.id", index=True, nullable=False)
    title: str = Field(default="New Conversation")
    model: str = Field(default="complex_reasoning")
    system_prompt: str | None = Field(default=None)
    context_summary: str | None = Field(default=None, description="Compressed conversation context summary")
    is_pinned: bool = Field(default=False)
    is_archived: bool = Field(default=False)
    token_count: int = Field(default=0)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC).replace(tzinfo=None))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC).replace(tzinfo=None))


class Message(SQLModel, table=True):
    __tablename__ = "messages"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True, index=True)
    conversation_id: uuid.UUID = Field(foreign_key="conversations.id", index=True, nullable=False)
    parent_message_id: uuid.UUID | None = Field(default=None, foreign_key="messages.id")
    role: str = Field(description="user | assistant | system | tool")
    content: str = Field(nullable=False)
    model: str | None = Field(default=None)
    thought_process: str | None = Field(default=None, description="CoT thinking content")
    reasoning: str | None = Field(default=None, description="CoT thinking content")
    prompt_tokens: int = Field(default=0)
    completion_tokens: int = Field(default=0)
    total_tokens: int = Field(default=0)
    tokens_used: int | None = Field(default=None)
    latency_ms: int | None = Field(default=None)
    citations: list[dict[str, Any]] = Field(default_factory=list, sa_column=Column(JSONB, nullable=True))
    tool_calls: list[dict[str, Any]] | None = Field(default=None, sa_column=Column(JSONB, nullable=True))
    tool_results: list[dict[str, Any]] | None = Field(default=None, sa_column=Column(JSONB, nullable=True))
    user_feedback: str | None = Field(default=None)
    feedback_note: str | None = Field(default=None)
    metadata_json: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSONB, nullable=True))
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC).replace(tzinfo=None))


class MessageAttachment(SQLModel, table=True):
    __tablename__ = "message_attachments"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    message_id: uuid.UUID = Field(foreign_key="messages.id", index=True, nullable=False)
    file_id: uuid.UUID = Field(foreign_key="files.id", index=True, nullable=False)
    filename: str = Field(nullable=False)
    file_type: str = Field(nullable=False)
    file_size_bytes: int = Field(default=0)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC).replace(tzinfo=None))


class ConversationBranch(SQLModel, table=True):
    __tablename__ = "conversation_branches"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    conversation_id: uuid.UUID = Field(foreign_key="conversations.id", unique=True, nullable=False, description="Child (forked) conversation")
    parent_conversation_id: uuid.UUID = Field(foreign_key="conversations.id", index=True, nullable=False)
    fork_message_id: uuid.UUID = Field(foreign_key="messages.id", nullable=False)
    branch_name: str | None = Field(default=None)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC).replace(tzinfo=None))
