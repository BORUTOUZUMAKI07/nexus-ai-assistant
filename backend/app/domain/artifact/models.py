"""
Artifact Domain Models.

An ``Artifact`` is a persisted, versioned AI-generated file (docs, specs, code,
slides source). The current content lives on the row; every prior revision is
snapshotted into ``artifact_versions`` when a new version is written, so a chat
regenerating a document never destroys the previous revision.
"""
import uuid
from datetime import UTC, datetime

from sqlalchemy import Text
from sqlmodel import Field, SQLModel


class Artifact(SQLModel, table=True):
    __tablename__ = "artifacts"
    __table_args__ = {"extend_existing": True}

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True, index=True)
    user_id: uuid.UUID = Field(foreign_key="users.id", index=True, nullable=False)
    conversation_id: uuid.UUID | None = Field(
        default=None, foreign_key="conversations.id", index=True
    )
    message_id: uuid.UUID | None = Field(
        default=None, foreign_key="messages.id", index=True
    )
    title: str = Field(nullable=False)
    language: str = Field(default="markdown", description="python | markdown | typescript | …")
    mime_type: str = Field(default="text/plain")
    content: str = Field(nullable=False, sa_type=Text)
    version: int = Field(default=1)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC).replace(tzinfo=None))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC).replace(tzinfo=None))


class ArtifactVersion(SQLModel, table=True):
    __tablename__ = "artifact_versions"
    __table_args__ = {"extend_existing": True}

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True, index=True)
    artifact_id: uuid.UUID = Field(foreign_key="artifacts.id", index=True, nullable=False)
    version: int = Field(nullable=False)
    title: str = Field(nullable=False)
    language: str = Field(default="markdown")
    mime_type: str = Field(default="text/plain")
    content: str = Field(nullable=False, sa_type=Text)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC).replace(tzinfo=None))
