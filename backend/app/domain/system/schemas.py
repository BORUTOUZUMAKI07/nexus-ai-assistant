"""
Pydantic Schemas for System & Audit Domain (Pydantic V2 Standard).
"""
from typing import Any
from uuid import UUID

from pydantic import BaseModel


class AuditLogCreate(BaseModel):
    user_id: UUID | None = None
    action: str
    resource_type: str
    resource_id: str | None = None
    ip_address: str | None = None
    user_agent: str | None = None
    status: str = "success"
    details: dict[str, Any] = {}
