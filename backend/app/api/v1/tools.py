"""
Tools API Router.
Pure HTTP transport layer — delegates tool execution and HITL approval to ToolService (SRP + DIP).
"""
from typing import Any
from uuid import UUID

from backend.app.api.deps import (
    get_conversation_service,
    get_current_user,
    get_db,
    get_tool_service,
    require_idempotency_key,
)
from backend.app.core.exceptions import ResourceNotFoundError
from backend.app.domain.tool.repository import ToolRepository
from backend.app.domain.tool.schemas import ToolApprovalRequest
from backend.app.domain.user.models import User
from backend.app.infrastructure.resilience.rate_limit import rate_limit
from backend.app.mcp.client import mcp_client
from backend.app.services.conversation_service import ConversationService
from backend.app.services.tool_service import ToolService
from backend.app.services.tools.elicitations import ElicitationService
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

router = APIRouter(prefix="/tools", tags=["tools"])


class ToolExecuteRequest(BaseModel):
    tool_name: str
    arguments: dict[str, Any]
    conversation_id: UUID
    # NOTE: there is intentionally NO is_user_approved field here. Approval is a
    # server-side decision (SEC-01): a client must never be able to declare its
    # own approval. Approval-required tools return status=requires_approval plus
    # a tool_call_id; the human approves that exact call via POST /tools/approval,
    # and only THEN does the server execute it.


@router.get("", response_model=list[dict[str, Any]])
async def list_available_tools(
    current_user: User = Depends(get_current_user),
):
    """Returns all tools available for LLM function calling (including FastMCP tools)."""
    return await mcp_client.list_available_tools()


@router.post("/execute", response_model=dict[str, Any])
async def execute_tool_endpoint(
    req: ToolExecuteRequest,
    current_user: User = Depends(get_current_user),
    tool_svc: ToolService = Depends(get_tool_service),
    conv_svc: ConversationService = Depends(get_conversation_service),
    _idem_key: None = require_idempotency_key("tool.execute"),
    _rl: None = rate_limit("tool.execute", limit=20, window_seconds=60, org_scope=True),
):
    """Directly execute a vetted tool through the 5-step safety gateway."""
    # IDOR guard: the tool call is logged against this conversation — verify the
    # caller actually owns it before executing/logging.
    try:
        await conv_svc.get_conversation(req.conversation_id, user_id=current_user.id)
    except ResourceNotFoundError:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return await tool_svc.execute_tool(
        tool_name=req.tool_name,
        arguments=req.arguments,
        conversation_id=req.conversation_id,
        user_id=current_user.id,
    )


@router.post("/approval")
async def approve_tool_call(
    approval: ToolApprovalRequest,
    current_user: User = Depends(get_current_user),
    tool_svc: ToolService = Depends(get_tool_service),
    _idem_key: None = require_idempotency_key("tool.approval"),
):
    """Resolves a pending Human-In-The-Loop approval request."""
    try:
        return await tool_svc.approve_tool_call(approval, user_id=current_user.id)
    except ResourceNotFoundError as exc:
        raise HTTPException(status_code=404, detail=exc.message)


class ElicitationRequest(BaseModel):
    conversation_id: UUID
    message: str
    json_schema: dict[str, Any]
    title: str = "Action needed"


class ElicitationResponseBody(BaseModel):
    elicitation_id: UUID
    answer: dict[str, Any]


def _validate_elicitation_answer(answer: dict[str, Any], schema: dict[str, Any]) -> None:
    """Lightweight required-property validation against the requested JSON Schema."""
    required = schema.get("required", []) if isinstance(schema, dict) else []
    if not isinstance(answer, dict):
        raise HTTPException(status_code=422, detail="Answer must be a JSON object")
    missing = [prop for prop in required if isinstance(prop, str) and prop not in answer]
    if missing:
        raise HTTPException(status_code=422, detail=f"Answer missing required properties: {', '.join(missing)}")


@router.post("/elicitations")
async def park_elicitation(
    req: ElicitationRequest,
    current_user: User = Depends(get_current_user),
    conv_svc: ConversationService = Depends(get_conversation_service),
    session=Depends(get_db),
):
    """
    Park a structured human-input request (MCP elicitation) against an owned
    conversation. The agent can pause mid-task with a schema-validated question;
    the human answers via POST /tools/elicitations/{id}/respond. Single-use +
    expiry semantics match the verified HITL approval flow (SEC-06).
    """
    if not req.message.strip():
        raise HTTPException(status_code=422, detail="message is required")
    if not isinstance(req.json_schema, dict) or not req.json_schema:
        raise HTTPException(status_code=422, detail="json_schema must be a non-empty JSON Schema object")
    # IDOR guard: verify the caller actually owns the conversation.
    try:
        await conv_svc.get_conversation(req.conversation_id, user_id=current_user.id)
    except ResourceNotFoundError:
        raise HTTPException(status_code=404, detail="Conversation not found")

    service = ElicitationService(session)
    return await service.park_elicitation(
        conversation_id=req.conversation_id,
        schema=req.json_schema,
        message=req.message,
        title=req.title,
    )


@router.post("/elicitations/{elicitation_id}/respond")
async def respond_elicitation(
    elicitation_id: UUID,
    body: ElicitationResponseBody,
    current_user: User = Depends(get_current_user),
    session=Depends(get_db),
):
    """
    Resolve a parked elicitation with the caller's structured answer. Mirrors
    approval semantics: 404 unknown/not-owned → 410 expired → 409 already
    resolved. Required properties declared in the parked schema are enforced.
    """
    service = ElicitationService(session)
    # Owner-scoped lookup for the parked schema (validation only — the resolve
    # itself re-validates ownership and single-use atomically).
    repo = ToolRepository(session)
    pending = await repo.get_tool_call(elicitation_id, user_id=current_user.id)
    if not pending or getattr(pending, "tool_name", None) != "__elicitation__":
        raise HTTPException(status_code=404, detail="Elicitation not found")
    schema = (pending.input_args or {}).get("schema", {})
    if body.elicitation_id != elicitation_id:
        raise HTTPException(status_code=422, detail="elicitation_id mismatch")
    _validate_elicitation_answer(body.answer, schema)
    return await service.resolve_elicitation(
        elicitation_id=elicitation_id,
        user_id=current_user.id,
        answer=body.answer,
    )
