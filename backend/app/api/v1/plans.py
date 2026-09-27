"""
Plans API Router — plan-then-approve flow.

Endpoint surface:
  POST /conversations/{conversation_id}/plan   create a plan for a task (no tools run)
  GET  /conversations/{conversation_id}/plans  list plans of a conversation
  POST /plans/{plan_id}/approve                approve (only then may execution use it)
  POST /plans/{plan_id}/reject                 reject with an optional human reason

Every read/mutation is owner-scoped (IDOR): plans belong to the requesting user
and only pending plans can transition.
"""
from uuid import UUID

from backend.app.api.deps import (
    get_conversation_service,
    get_current_user,
    get_plan_service,
)
from backend.app.core.exceptions import (
    ApprovalConsumedError,
    ResourceNotFoundError,
)
from backend.app.domain.plan.schemas import (
    PlanDetailResponse,
    PlanRejectRequest,
    PlanRequest,
)
from backend.app.domain.user.models import User
from backend.app.services.conversation_service import ConversationService
from backend.app.services.plan_service import PlanService
from fastapi import APIRouter, Depends, HTTPException

router = APIRouter(tags=["plans"])


def _not_found(path: str) -> HTTPException:
    return HTTPException(status_code=404, detail=f"{path} not found")


@router.post(
    "/conversations/{conversation_id}/plan",
    response_model=PlanDetailResponse,
    status_code=201,
)
async def create_plan(
    conversation_id: UUID,
    body: PlanRequest,
    current_user: User = Depends(get_current_user),
    conv_svc: ConversationService = Depends(get_conversation_service),
    plan_svc: PlanService = Depends(get_plan_service),
) -> PlanDetailResponse:
    """Draft a plan for ``task`` against a conversation the user owns.

    Deliberately tool-free: nothing executes until the human approves the plan.
    """
    try:
        await conv_svc.get_conversation(conversation_id, user_id=current_user.id)
    except ResourceNotFoundError:
        raise _not_found("Conversation")
    plan = await plan_svc.prepare_plan(
        conversation_id=conversation_id,
        user_id=current_user.id,
        task=body.task,
    )
    return PlanDetailResponse.model_validate(plan)


@router.get(
    "/conversations/{conversation_id}/plans",
    response_model=list[PlanDetailResponse],
)
async def list_plans(
    conversation_id: UUID,
    limit: int = 20,
    current_user: User = Depends(get_current_user),
    conv_svc: ConversationService = Depends(get_conversation_service),
    plan_svc: PlanService = Depends(get_plan_service),
) -> list[PlanDetailResponse]:
    """List the user's plans for a conversation, newest first."""
    try:
        await conv_svc.get_conversation(conversation_id, user_id=current_user.id)
    except ResourceNotFoundError:
        raise _not_found("Conversation")
    plans = await plan_svc.list_plans(conversation_id, current_user.id, limit=limit)
    return [PlanDetailResponse.model_validate(p) for p in plans]


@router.post("/plans/{plan_id}/approve", response_model=PlanDetailResponse)
async def approve_plan(
    plan_id: UUID,
    current_user: User = Depends(get_current_user),
    plan_svc: PlanService = Depends(get_plan_service),
) -> PlanDetailResponse:
    """Approve a pending plan — the explicit gate before execution starts."""
    try:
        plan = await plan_svc.approve(plan_id, current_user.id)
    except ResourceNotFoundError:
        raise _not_found("Plan")
    except ApprovalConsumedError as exc:
        raise HTTPException(status_code=409, detail=exc.message)
    return PlanDetailResponse.model_validate(plan)


@router.post("/plans/{plan_id}/reject", response_model=PlanDetailResponse)
async def reject_plan(
    plan_id: UUID,
    body: PlanRejectRequest | None = None,
    current_user: User = Depends(get_current_user),
    plan_svc: PlanService = Depends(get_plan_service),
) -> PlanDetailResponse:
    """Reject a pending plan (optional human reason recorded)."""
    try:
        plan = await plan_svc.reject(
            plan_id, current_user.id, reason=body.reason if body else None
        )
    except ResourceNotFoundError:
        raise _not_found("Plan")
    except ApprovalConsumedError as exc:
        raise HTTPException(status_code=409, detail=exc.message)
    return PlanDetailResponse.model_validate(plan)
