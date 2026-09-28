"""
Pydantic Schemas for Tool Domain (Pydantic V2 Standard).
"""
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class ToolCreate(BaseModel):
    name: str = Field(min_length=2, max_length=100, pattern=r"^[a-zA-Z0-9_.-]+$")
    description: str = Field(min_length=1, max_length=2000)
    category: str = Field(default="general", min_length=1, max_length=100)
    parameters_schema: dict[str, Any] = Field(default_factory=dict)
    requires_approval: bool = False
    is_system: bool = True
    timeout_seconds: int = Field(default=30, ge=1, le=300)


class ToolUpdate(BaseModel):
    description: str | None = Field(default=None, min_length=1, max_length=2000)
    parameters_schema: dict[str, Any] | None = None
    requires_approval: bool | None = None
    is_enabled: bool | None = None
    timeout_seconds: int | None = Field(default=None, ge=1, le=300)


class ToolApprovalRequest(BaseModel):
    # Approval requests accept only the explicit decision contract.
    model_config = ConfigDict(extra="forbid")

    tool_call_id: UUID
    approved: bool
    reason: str | None = Field(default=None, max_length=1000)
