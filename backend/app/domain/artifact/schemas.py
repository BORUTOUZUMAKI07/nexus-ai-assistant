"""
Pydantic Schemas for Artifact Domain (Pydantic V2 Standard).
"""
from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class ArtifactVersionSnapshot(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    artifact_id: UUID
    version: int
    title: str
    language: str
    mime_type: str
    content: str
    created_at: datetime


class ArtifactCreate(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    language: str = Field(default="markdown", max_length=50)
    mime_type: str = Field(default="text/plain", max_length=100)
    content: str = Field(min_length=1, max_length=200_000)
    conversation_id: UUID | None = None
    message_id: UUID | None = None


class ArtifactVersionCreate(BaseModel):
    content: str = Field(min_length=1, max_length=200_000)
    title: str | None = Field(default=None, max_length=200)
    language: str | None = Field(default=None, max_length=50)
    mime_type: str | None = Field(default=None, max_length=100)


class ArtifactResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    user_id: UUID
    conversation_id: UUID | None = None
    message_id: UUID | None = None
    title: str
    language: str
    mime_type: str
    content: str
    version: int
    created_at: datetime
    updated_at: datetime


class ArtifactDetailResponse(ArtifactResponse):
    versions: list[ArtifactVersionSnapshot] = Field(default_factory=list)
