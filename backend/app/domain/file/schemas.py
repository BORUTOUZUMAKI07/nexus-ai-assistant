"""
Pydantic Schemas for File & RAG Domain (Pydantic V2 Standard).
"""
from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class FileResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    user_id: UUID
    conversation_id: UUID | None
    filename: str
    original_filename: str
    file_type: str
    mime_type: str
    size_bytes: int
    storage_path: str
    status: str
    error_message: str | None
    chunk_count: int
    created_at: datetime
    updated_at: datetime


class FileChunkResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    file_id: UUID
    chunk_index: int
    content: str
    token_count: int
    metadata_json: dict[str, Any]
    qdrant_point_id: str | None


class RAGQueryRequest(BaseModel):
    query: str = Field(min_length=1)
    file_ids: list[UUID] | None = None
    conversation_id: UUID | None = None
    top_k: int = Field(default=5, ge=1, le=20)
    score_threshold: float = Field(default=0.4, ge=0.0, le=1.0)


class RAGCitation(BaseModel):
    file_id: UUID
    filename: str
    chunk_index: int
    score: float
    content_snippet: str
    metadata: dict[str, Any] = {}


class RAGQueryResult(BaseModel):
    query: str
    citations: list[RAGCitation]
    total_retrieved: int
    query_variants: list[str] = Field(default_factory=list)
