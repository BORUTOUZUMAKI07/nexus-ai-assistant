"""Tools endpoints integration tests: discovery, execution, HITL approval.

The fastmcp/redis/sandbox boundary is stubbed autouse (see integration
conftest) so the real ToolService + ToolRepository persist calls to sqlite.
"""
import uuid

import pytest
from backend.app.domain.tool.models import ToolCall
from backend.app.domain.tool.repository import ToolRepository
from sqlmodel import select


@pytest.fixture
async def conversation_id(client, user_auth_headers):
    """A fresh conversation owned by the authenticated test user."""
    resp = await client.post(
        "/api/v1/conversations",
        json={"title": "Tools Chat"},
        headers=user_auth_headers,
    )
    assert resp.status_code == 201
    return resp.json()["id"]


@pytest.mark.asyncio
async def test_list_tools_returns_discovered_tools(client, user_auth_headers):
    resp = await client.get("/api/v1/tools", headers=user_auth_headers)
    assert resp.status_code == 200
    names = [t["function"]["name"] for t in resp.json()]
    assert "web_search" in names


@pytest.mark.asyncio
async def test_execute_tool_persists_call(
    client, user_auth_headers, db_session, conversation_id
):
    resp = await client.post(
        "/api/v1/tools/execute",
        json={
            "tool_name": "web_search",
            "arguments": {"query": "integration testing"},
            "conversation_id": str(conversation_id),
        },
        headers=user_auth_headers,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "completed"
    assert body["result"]["summary"] == "Executed web_search"

    calls = (
        await db_session.exec(
            select(ToolCall).where(ToolCall.conversation_id == conversation_id)
        )
    ).all()
    assert len(calls) == 1
    assert calls[0].status == "completed"
    assert calls[0].input_args == {"query": "integration testing"}


@pytest.mark.asyncio
async def test_execute_tool_requires_auth(client):
    resp = await client.post(
        "/api/v1/tools/execute",
        json={
            "tool_name": "web_search",
            "arguments": {"query": "x"},
            "conversation_id": str(uuid.uuid4()),
        },
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_approve_tool_call_updates_status(
    client, user_auth_headers, db_session, test_user, conversation_id
):
    repo = ToolRepository(db_session)
    call = await repo.log_tool_call(
        conversation_id=conversation_id,
        tool_name="web_search",
        input_args={"query": "pending"},
        status="pending",
        requires_approval=True,
    )

    resp = await client.post(
        "/api/v1/tools/approval",
        json={
            "tool_call_id": str(call.id),
            "approved": True,
            "reason": "looks safe",
        },
        headers=user_auth_headers,
    )
    assert resp.status_code == 200
    assert resp.json()["approved"] is True

    await db_session.refresh(call)
    refreshed = await repo.get_tool_call(call.id)
    assert refreshed.status == "approved"
    assert refreshed.is_approved is True


@pytest.mark.asyncio
async def test_approve_unknown_tool_call_404(client, user_auth_headers):
    resp = await client.post(
        "/api/v1/tools/approval",
        json={
            "tool_call_id": str(uuid.uuid4()),
            "approved": False,
        },
        headers=user_auth_headers,
    )
    assert resp.status_code == 404
