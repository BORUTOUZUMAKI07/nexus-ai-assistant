"""
Domain models for System Configuration and Compliance Audit Logs.
"""
from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Column, Field, SQLModel


class SystemConfig(SQLModel, table=True):
    __tablename__ = "system_configs"

    id: UUID = Field(default_factory=uuid4, primary_key=True, index=True)
    key: str = Field(unique=True, index=True)
    value_json: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSONB))
    category: str = Field(default="general", index=True)
    description: str | None = Field(default=None)
    is_secret: bool = Field(default=False)
    updated_by: UUID | None = Field(default=None)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class AuditLog(SQLModel, table=True):
    __tablename__ = "audit_logs"

    id: UUID = Field(default_factory=uuid4, primary_key=True, index=True)
    user_id: UUID | None = Field(default=None, index=True)
    action: str = Field(index=True)  # tool_execution, model_inference, file_upload, role_change, hitl_approval
    resource_type: str = Field(index=True)  # tool, file, conversation, user, config
    resource_id: str | None = Field(default=None, index=True)
    ip_address: str | None = Field(default=None)
    user_agent: str | None = Field(default=None)
    status: str = Field(default="success")  # success, denied, failed
    details: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSONB))
    created_at: datetime = Field(default_factory=datetime.utcnow, index=True)
