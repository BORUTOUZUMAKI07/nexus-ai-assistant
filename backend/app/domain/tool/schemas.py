"""
Pydantic Schemas for Tool Domain (Pydantic V2 Standard).
"""
from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class ToolCreate(BaseModel):
    name: str = Field(min_length=2, max_length=100)
    description: str
    category: str = "general"
    parameters_schema: dict[str, Any] = Field(default_factory=dict)
    requires_approval: bool = False
    is_system: bool = True
    timeout_seconds: int = 30


class ToolUpdate(BaseModel):
    description: str | None = None
    parameters_schema: dict[str, Any] | None = None
    requires_approval: bool | None = None
    is_enabled: bool | None = None
    timeout_seconds: int | None = None


class ToolResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    description: str
    category: str
    parameters_schema: dict[str, Any]
    requires_approval: bool
    is_system: bool
    is_enabled: bool
    timeout_seconds: int
    created_at: datetime
    updated_at: datetime


class ToolCallResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    conversation_id: UUID
    message_id: UUID | None
    tool_name: str
    input_args: dict[str, Any]
    output_result: dict[str, Any] | None
    status: str
    error_message: str | None
    execution_time_ms: float
    requires_approval: bool
    is_approved: bool | None
    created_at: datetime


class ToolApprovalRequest(BaseModel):
    tool_call_id: UUID
    approved: bool
    reason: str | None = None
