"""
Pydantic Schemas for Hook Domain (Pydantic V2 Standard).
"""
from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class HookPolicyCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    tool_name: str = Field(min_length=1, max_length=120, description="'*' = every tool")
    event: str = Field(default="pre_tool", pattern="^(pre_tool|post_tool)$")
    org_id: UUID | None = Field(default=None)
    action: str = Field(default="log", pattern="^(block|redact|log)$")
    field: str | None = Field(default=None, max_length=120)
    message: str | None = Field(default=None, max_length=500)
    enabled: bool = True


class HookPolicyUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    tool_name: str | None = Field(default=None, min_length=1, max_length=120)
    event: str | None = Field(default=None, pattern="^(pre_tool|post_tool)$")
    org_id: UUID | None = None
    action: str | None = Field(default=None, pattern="^(block|redact|log)$")
    field: str | None = Field(default=None, max_length=120)
    message: str | None = Field(default=None, max_length=500)
    enabled: bool | None = None


class HookPolicyResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    tool_name: str
    event: str
    org_id: UUID | None = None
    action: str
    field: str | None = None
    message: str | None = None
    enabled: bool = True
    created_at: datetime
    updated_at: datetime
