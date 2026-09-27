"""Domain model for read-only conversation sharing."""
import uuid
from datetime import UTC, datetime

from sqlmodel import Field, SQLModel


class ConversationShare(SQLModel, table=True):
    __tablename__ = "conversation_shares"
    __table_args__ = {"extend_existing": True}

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True, index=True)
    conversation_id: uuid.UUID = Field(foreign_key="conversations.id", index=True, nullable=False)
    created_by: uuid.UUID = Field(foreign_key="users.id", index=True, nullable=False)
    token: str = Field(unique=True, index=True, nullable=False, description="Unguessable share token (URL-safe)")
    is_active: bool = Field(default=True)
    expires_at: datetime | None = Field(default=None, description="Optional TTL for the share link")
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC).replace(tzinfo=None))