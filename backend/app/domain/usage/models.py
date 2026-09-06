"""
Domain models for Usage, Cost, and Evaluation telemetry.
"""
from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Column, Field, SQLModel


class UsageLog(SQLModel, table=True):
    __tablename__ = "usage_logs"

    id: UUID = Field(default_factory=uuid4, primary_key=True, index=True)
    user_id: UUID = Field(index=True, foreign_key="users.id")
    conversation_id: UUID | None = Field(default=None, index=True, foreign_key="conversations.id")
    message_id: UUID | None = Field(default=None, index=True)
    model: str = Field(index=True)
    provider: str = Field(default="groq", index=True)
    prompt_tokens: int = Field(default=0)
    completion_tokens: int = Field(default=0)
    total_tokens: int = Field(default=0)
    cached_tokens: int = Field(default=0)
    latency_ms: float = Field(default=0.0)
    cost_usd: float = Field(default=0.0)
    status: str = Field(default="success")
    error_message: str | None = Field(default=None)
    metadata_json: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSONB))
    created_at: datetime = Field(default_factory=datetime.utcnow, index=True)


class CostLog(SQLModel, table=True):
    __tablename__ = "cost_logs"

    id: UUID = Field(default_factory=uuid4, primary_key=True, index=True)
    user_id: UUID = Field(index=True, foreign_key="users.id")
    provider: str = Field(index=True)
    model: str = Field(index=True)
    input_cost: float = Field(default=0.0)
    output_cost: float = Field(default=0.0)
    total_cost: float = Field(default=0.0)
    billing_period: str = Field(index=True)  # YYYY-MM
    created_at: datetime = Field(default_factory=datetime.utcnow, index=True)


class EvaluationLog(SQLModel, table=True):
    __tablename__ = "evaluation_logs"

    id: UUID = Field(default_factory=uuid4, primary_key=True, index=True)
    trace_id: str = Field(index=True)
    conversation_id: UUID | None = Field(default=None, index=True)
    message_id: UUID | None = Field(default=None, index=True)
    metric_name: str = Field(index=True)  # faithfulness, answer_relevancy, hallucination, latency, evidence_gate
    score: float = Field(default=0.0)
    passed: bool = Field(default=True)
    reason: str | None = Field(default=None)
    evaluator: str = Field(default="deepeval")  # deepeval, ragas, g-eval, guardrail
    metadata_json: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSONB))
    created_at: datetime = Field(default_factory=datetime.utcnow, index=True)
