"""
Domain models for the automated prompt-optimization loop (MD §6.15).

Each ``PromptOptimizationRun`` records one closed loop: propose K candidate
rewrites of a prompt key, score each candidate against a golden case set,
promote the winner if it beats the baseline, and persist the evidence trail
(candidate scores, accepted variant, score deltas) for audit.
"""
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Column, Field, SQLModel


class PromptOptimizationRun(SQLModel, table=True):
    __tablename__ = "prompt_optimization_runs"

    id: UUID = Field(default_factory=uuid4, primary_key=True, index=True)
    prompt_key: str = Field(index=True)  # e.g. chat_system_prompt, researcher
    # Text, not AutoString: these hold whole prompts, which are unbounded. The
    # explicit type also matches the migrated column, so autogenerate stays
    # quiet instead of proposing a table rewrite to VARCHAR.
    baseline_prompt: str = Field(default="", sa_column=Column(Text, nullable=False))
    status: str = Field(default="running")  # running | completed | failed
    candidate_count: int = Field(default=0)
    # Either the winning prompt text, or the literal "baseline" if none won.
    accepted_variant: str | None = Field(
        default=None, sa_column=Column(Text, nullable=True)
    )
    baseline_score: float = Field(default=0.0)
    best_score: float = Field(default=0.0)
    average_score: float = Field(default=0.0)
    details: dict[str, Any] = Field(
        default_factory=dict, sa_column=Column(JSONB, nullable=False)
    )
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC).replace(tzinfo=None), index=True
    )
