import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Column, Field, SQLModel


class File(SQLModel, table=True):
    __tablename__ = "files"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True, index=True)
    user_id: uuid.UUID = Field(foreign_key="users.id", index=True, nullable=False)
    conversation_id: uuid.UUID | None = Field(default=None, foreign_key="conversations.id", index=True)
    filename: str = Field(nullable=False)
    original_filename: str = Field(nullable=False)
    file_type: str = Field(description="pdf | docx | txt | md | py | js | image | other")
    mime_type: str = Field(nullable=False)
    size_bytes: int = Field(description="Size in bytes")
    storage_path: str = Field(nullable=False, description="Supabase storage path")
    status: str = Field(default="pending", description="pending | processing | indexed | failed")
    chunk_count: int = Field(default=0)
    error_message: str | None = Field(default=None)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC).replace(tzinfo=None))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC).replace(tzinfo=None))


class FileChunk(SQLModel, table=True):
    __tablename__ = "file_chunks"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    file_id: uuid.UUID = Field(foreign_key="files.id", index=True, nullable=False)
    chunk_index: int = Field(nullable=False)
    content: str = Field(nullable=False)
    contextual_summary: str | None = Field(default=None, description="Anthropic contextual summary pattern")
    token_count: int = Field(default=0)
    metadata_json: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSONB, nullable=True))
    qdrant_point_id: str | None = Field(default=None, index=True)
    parent_chunk_id: uuid.UUID | None = Field(
        default=None,
        foreign_key="file_chunks.id",
        index=True,
        description="Parent chunk (512t) this child chunk (128t) belongs to",
    )
    is_parent: bool = Field(default=False, description="True if this row is a 512-token parent chunk")
    contextual_prefix: str | None = Field(
        default=None,
        description="Document title + heading hierarchy prefix prepended to the child chunk",
    )
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC).replace(tzinfo=None))


class FileMetadata(SQLModel, table=True):
    __tablename__ = "file_metadata"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    file_id: uuid.UUID = Field(foreign_key="files.id", index=True, nullable=False)
    key: str = Field(nullable=False, index=True)
    value: str = Field(nullable=False)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC).replace(tzinfo=None))
