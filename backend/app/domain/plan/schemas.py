"""
Pydantic Schemas for Plan Domain (Pydantic V2 Standard).

``PlanRequest`` is the create payload (the task a plan is drafted for);
``PlanRejectRequest`` carries an optional human reason. Responses mirror the
``Plan`` row shape so the frontend can render the steps immediately.
"""
from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class PlanRequest(BaseModel):
    task: str = Field(
        min_length=1, max_length=8000, description="Task the plan should achieve"
    )


class PlanRejectRequest(BaseModel):
    reason: str | None = Field(default=None, max_length=2000)


class PlanResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    conversation_id: UUID
    user_id: UUID
    title: str
    summary: str | None = None
    steps: list[str] = Field(default_factory=list)
    status: str = "pending"
    decision_reason: str | None = None
    created_at: datetime
    updated_at: datetime
    decided_at: datetime | None = None


class PlanDetailResponse(PlanResponse):
    """Alias for the API layer — the detail view is the row itself."""
