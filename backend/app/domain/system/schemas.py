"""
Pydantic Schemas for System & Audit Domain (Pydantic V2 Standard).
"""
from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class SystemConfigCreate(BaseModel):
    key: str
    value_json: dict[str, Any]
    category: str = "general"
    description: str | None = None
    is_secret: bool = False


class SystemConfigResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    key: str
    value_json: dict[str, Any]
    category: str
    description: str | None
    is_secret: bool
    created_at: datetime
    updated_at: datetime


class AuditLogCreate(BaseModel):
    user_id: UUID | None = None
    action: str
    resource_type: str
    resource_id: str | None = None
    ip_address: str | None = None
    user_agent: str | None = None
    status: str = "success"
    details: dict[str, Any] = {}


class AuditLogResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    user_id: UUID | None
    action: str
    resource_type: str
    resource_id: str | None
    ip_address: str | None
    user_agent: str | None
    status: str
    details: dict[str, Any]
    created_at: datetime
