"""
Pydantic Schemas for Conversation Domain (Pydantic V2 Standard).
"""
from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class MessageAttachmentCreate(BaseModel):
    file_id: UUID
    filename: str
    file_type: str
    file_size_bytes: int


class MessageAttachmentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    file_id: UUID
    filename: str
    file_type: str
    file_size_bytes: int


class MessageCreate(BaseModel):
    content: str = Field(min_length=1)
    role: str = "user"
    parent_message_id: UUID | None = None
    model: str | None = None
    system_prompt_override: str | None = None
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
    title: str | None = "New Chat"
    model: str | None = "llama-3.3-70b-versatile"
    system_prompt: str | None = None
    is_pinned: bool | None = False


class ConversationUpdate(BaseModel):
    title: str | None = None
    is_pinned: bool | None = None
    is_archived: bool | None = None
    system_prompt: str | None = None
    model: str | None = None


class ConversationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    user_id: UUID
    title: str
    model: str
    is_pinned: bool
    is_archived: bool
    token_count: int
    created_at: datetime
    updated_at: datetime


class ConversationDetailResponse(ConversationResponse):
    system_prompt: str | None
    messages: list[MessageResponse] = Field(default_factory=list)


class BranchCreate(BaseModel):
    fork_message_id: UUID
    branch_name: str


class BranchResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    conversation_id: UUID
    parent_conversation_id: UUID
    fork_message_id: UUID
    branch_name: str
    created_at: datetime
