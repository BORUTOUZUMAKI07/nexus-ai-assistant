import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import Float
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlmodel import Column, Field, SQLModel


class Tool(SQLModel, table=True):
    __tablename__ = "tools"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    name: str = Field(unique=True, index=True, nullable=False)
    description: str = Field(nullable=False)
    category: str = Field(default="general", index=True)
    parameters_schema: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSONB, nullable=False))
    timeout_seconds: int = Field(default=30)
    is_system: bool = Field(default=False)
    is_enabled: bool = Field(default=True)
    requires_approval: bool = Field(default=False)
    embedding: list[float] | None = Field(default=None, sa_column=Column(ARRAY(Float), nullable=True))
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC).replace(tzinfo=None))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC).replace(tzinfo=None))


class ToolCall(SQLModel, table=True):
    __tablename__ = "tool_calls"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True, index=True)
    conversation_id: uuid.UUID = Field(foreign_key="conversations.id", index=True, nullable=False)
    message_id: uuid.UUID | None = Field(default=None, foreign_key="messages.id", index=True)
    tool_name: str = Field(nullable=False, index=True)
    input_args: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSONB, nullable=False))
    output_result: dict[str, Any] | None = Field(default=None, sa_column=Column(JSONB, nullable=True))
    status: str = Field(default="pending", description="pending | running | completed | failed | rejected")
    error_message: str | None = Field(default=None)
    execution_time_ms: float = Field(default=0.0)
    requires_approval: bool = Field(default=False)
    is_approved: bool | None = Field(default=None)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC).replace(tzinfo=None))


class ToolPermission(SQLModel, table=True):
    __tablename__ = "tool_permissions"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    user_id: uuid.UUID = Field(foreign_key="users.id", index=True, nullable=False)
    tool_id: uuid.UUID = Field(foreign_key="tools.id", index=True, nullable=False)
    is_allowed: bool = Field(default=True)
    granted_at: datetime = Field(default_factory=lambda: datetime.now(UTC).replace(tzinfo=None))
