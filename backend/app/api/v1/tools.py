"""
Tools API Router.
Pure HTTP transport layer — delegates tool execution and HITL approval to ToolService (SRP + DIP).
"""
from typing import Any
from uuid import UUID

from backend.app.api.deps import get_conversation_service, get_current_user, get_tool_service
from backend.app.core.exceptions import ResourceNotFoundError
from backend.app.domain.tool.schemas import ToolApprovalRequest
from backend.app.domain.user.models import User
from backend.app.mcp.client import mcp_client
from backend.app.services.conversation_service import ConversationService
from backend.app.services.tool_service import ToolService
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

router = APIRouter(prefix="/tools", tags=["tools"])


class ToolExecuteRequest(BaseModel):
    tool_name: str
    arguments: dict[str, Any]
    conversation_id: UUID


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
):
    """Execute a vetted tool through the gateway; approval cannot be asserted by clients."""
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
):
    """Resolves a pending Human-In-The-Loop approval request."""
    try:
        result = await tool_svc.approve_tool_call(approval, user_id=current_user.id)
    except ResourceNotFoundError as exc:
        raise HTTPException(status_code=404, detail=exc.message)

    # Translate the service's domain outcome into the appropriate HTTP status.
    # A replayed or concurrently resolved approval must not look like success.
    if result.get("status") == "conflict":
        raise HTTPException(status_code=409, detail=result.get("message", "Approval conflict"))
    return result
