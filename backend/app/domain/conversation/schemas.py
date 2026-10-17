"""
Pydantic Schemas for Conversation Domain (Pydantic V2 Standard).
"""
import uuid
from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class MessageAttachmentCreate(BaseModel):
    file_id: UUID
    filename: str
    file_type: str
    file_size_bytes: int


class MessageCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    content: str = Field(min_length=1, max_length=100000)
    role: str = "user"
    parent_message_id: UUID | None = None
    model: str | None = None
    system_prompt_override: str | None = Field(default=None, max_length=20000)
    attachments: list[MessageAttachmentCreate] | None = None
    tool_calls: list[dict[str, Any]] | None = None


class MessageResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    conversation_id: UUID
    parent_message_id: UUID | None
    role: str
    content: str
    thought_process: str | None = None
    model: str | None = None
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    citations: list[dict[str, Any]] = Field(default_factory=list)
    tool_calls: list[dict[str, Any]] = Field(default_factory=list)
    user_feedback: str | None = None
    feedback_note: str | None = None
    metadata_json: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime


class MessageFeedback(BaseModel):
    feedback: str = Field(pattern="^(thumbs_up|thumbs_down|flagged)$")
    feedback_note: str | None = None


class ConversationCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str | None = Field(default="New Chat", max_length=200)
    model: str | None = Field(default="llama-3.3-70b-versatile", max_length=128)
    system_prompt: str | None = Field(default=None, max_length=20000)
    is_pinned: bool | None = False


class ConversationUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str | None = Field(default=None, max_length=200)
    is_pinned: bool | None = None
    is_archived: bool | None = None
    project_id: uuid.UUID | None = None
    system_prompt: str | None = Field(default=None, max_length=20000)
    model: str | None = Field(default=None, max_length=128)


class ConversationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    user_id: UUID
    title: str
    model: str
    project_id: UUID | None = None
    is_pinned: bool
    is_archived: bool
    token_count: int
    created_at: datetime
    updated_at: datetime


class ConversationDetailResponse(ConversationResponse):
    system_prompt: str | None
    messages: list[MessageResponse] = Field(default_factory=list)


class BranchCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # Optional. A turn that was streamed live carries a client-side `msg-<ts>`
    # id until the transcript is reloaded from the server, so the client often
    # cannot name a message UUID here; omitting it forks at the latest message,
    # which is the only fork point the UI offers. See
    # `ConversationRepository.fork_conversation` for the resolution and the
    # membership check that keeps a foreign id from reaching the branch FK.
    fork_message_id: UUID | None = None
    branch_name: str = Field(min_length=1, max_length=120)


class ProjectCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=1000)


class ProjectUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=1000)


class ProjectResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    user_id: UUID
    name: str
    description: str | None = None
    created_at: datetime
    updated_at: datetime
