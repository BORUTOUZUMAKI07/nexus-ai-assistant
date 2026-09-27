"""Domain models for signed outbound webhooks."""
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Column, Field, SQLModel


class WebhookEndpoint(SQLModel, table=True):
    __tablename__ = "webhook_endpoints"
    __table_args__ = {"extend_existing": True}

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True, index=True)
    user_id: uuid.UUID = Field(foreign_key="users.id", index=True, nullable=False)
    url: str = Field(nullable=False, description="HTTPS callback URL")
    secret: str | None = Field(default=None, description="Optional per-endpoint HMAC signing secret")
    events: list[str] = Field(
        default_factory=lambda: ["message.completed"],
        sa_column=Column(JSONB, nullable=True),
        description="Subscribed event names, e.g. message.completed",
    )
    is_active: bool = Field(default=True)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC).replace(tzinfo=None))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC).replace(tzinfo=None))


class WebhookDelivery(SQLModel, table=True):
    __tablename__ = "webhook_deliveries"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True, index=True)
    endpoint_id: uuid.UUID = Field(foreign_key="webhook_endpoints.id", index=True, nullable=False)
    event: str = Field(nullable=False)
    payload_json: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSONB, nullable=True))
    status: str = Field(default="pending", description="pending | delivered | failed")
    http_status: int | None = Field(default=None)
    error_message: str | None = Field(default=None)
    attempts: int = Field(default=0)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC).replace(tzinfo=None))
    delivered_at: datetime | None = Field(default=None)