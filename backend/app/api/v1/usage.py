"""
Usage & Telemetry API Router.
Pure HTTP transport layer — delegates to UsageService (SRP + DIP).
"""
from uuid import UUID

from backend.app.api.deps import get_current_user, get_usage_service
from backend.app.domain.usage.schemas import EvaluationLogResponse, UsageSummaryResponse
from backend.app.domain.user.models import User
from backend.app.services.usage_service import UsageService
from fastapi import APIRouter, Depends

router = APIRouter(prefix="/usage", tags=["usage"])


@router.get("/summary", response_model=UsageSummaryResponse)
async def get_usage_summary(
    current_user: User = Depends(get_current_user),
    usage_svc: UsageService = Depends(get_usage_service),
):
    return await usage_svc.get_summary(current_user.id)


@router.get("/evaluations", response_model=list[EvaluationLogResponse])
async def get_evaluations(
    conversation_id: UUID | None = None,
    limit: int = 50,
    current_user: User = Depends(get_current_user),
    usage_svc: UsageService = Depends(get_usage_service),
):
    return await usage_svc.get_evaluations(
        conversation_id=conversation_id, limit=limit
    )
