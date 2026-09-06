"""
Pydantic Schemas for Usage & Telemetry Domain (Pydantic V2 Standard).
"""
from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class UsageLogCreate(BaseModel):
    user_id: UUID
    conversation_id: UUID | None = None
    message_id: UUID | None = None
    model: str
    provider: str = "groq"
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cached_tokens: int = 0
    latency_ms: float = 0.0
    cost_usd: float = 0.0
    status: str = "success"
    error_message: str | None = None
    metadata_json: dict[str, Any] = {}


class UsageSummaryResponse(BaseModel):
    total_tokens: int
    prompt_tokens: int
    completion_tokens: int
    cached_tokens: int
    total_cost_usd: float
    total_requests: int
    average_latency_ms: float


class EvaluationLogCreate(BaseModel):
    trace_id: str
    conversation_id: UUID | None = None
    message_id: UUID | None = None
    metric_name: str
    score: float
    passed: bool = True
    reason: str | None = None
    evaluator: str = "deepeval"
    metadata_json: dict[str, Any] = {}


class EvaluationLogResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    trace_id: str
    conversation_id: UUID | None
    message_id: UUID | None
    metric_name: str
    score: float
    passed: bool
    reason: str | None
    evaluator: str
    created_at: datetime
