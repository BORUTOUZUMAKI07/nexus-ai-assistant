"""
Tools API Router.
Pure HTTP transport layer — delegates tool execution and HITL approval to ToolService (SRP + DIP).
"""
from typing import Any
from uuid import UUID

from backend.app.api.deps import get_current_user, get_tool_service
from backend.app.core.exceptions import ResourceNotFoundError
from backend.app.domain.tool.schemas import ToolApprovalRequest
from backend.app.domain.user.models import User
from backend.app.mcp.client import mcp_client
from backend.app.services.tool_service import ToolService
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

router = APIRouter(prefix="/tools", tags=["tools"])


class ToolExecuteRequest(BaseModel):
    tool_name: str
    arguments: dict[str, Any]
    conversation_id: UUID
    is_user_approved: bool = False


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
):
    """Directly execute a vetted tool through the 5-step safety gateway."""
    return await tool_svc.execute_tool(
        tool_name=req.tool_name,
        arguments=req.arguments,
        conversation_id=req.conversation_id,
        user_id=current_user.id,
        is_user_approved=req.is_user_approved,
    )


@router.post("/approval")
async def approve_tool_call(
    approval: ToolApprovalRequest,
    current_user: User = Depends(get_current_user),
    tool_svc: ToolService = Depends(get_tool_service),
):
    """Resolves a pending Human-In-The-Loop approval request."""
    try:
        return await tool_svc.approve_tool_call(approval)
    except ResourceNotFoundError as exc:
        raise HTTPException(status_code=404, detail=exc.message)
