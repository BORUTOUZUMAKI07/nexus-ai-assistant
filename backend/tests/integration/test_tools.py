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
        status="requires_approval",
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
async def test_rejected_tool_call_is_terminal(
    client, user_auth_headers, db_session, conversation_id
):
    repo = ToolRepository(db_session)
    call = await repo.log_tool_call(
        conversation_id=conversation_id,
        tool_name="web_search",
        input_args={"query": "reject-me"},
        status="requires_approval",
        requires_approval=True,
    )

    resp = await client.post(
        "/api/v1/tools/approval",
        json={"tool_call_id": str(call.id), "approved": False, "reason": "not safe"},
        headers=user_auth_headers,
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "success"
    assert resp.json()["approved"] is False

    refreshed = await repo.get_tool_call(call.id)
    assert refreshed.status == "rejected"
    assert refreshed.is_approved is False

    replay = await client.post(
        "/api/v1/tools/approval",
        json={"tool_call_id": str(call.id), "approved": True},
        headers=user_auth_headers,
    )
    assert replay.status_code == 409


@pytest.mark.asyncio
async def test_approval_cannot_be_resolved_twice(
    client, user_auth_headers, db_session, conversation_id
):
    repo = ToolRepository(db_session)
    call = await repo.log_tool_call(
        conversation_id=conversation_id,
        tool_name="web_search",
        input_args={"query": "one-shot"},
        status="requires_approval",
        requires_approval=True,
    )

    payload = {
        "tool_call_id": str(call.id),
        "approved": True,
        "reason": "approved once",
    }
    first = await client.post(
        "/api/v1/tools/approval", json=payload, headers=user_auth_headers
    )
    assert first.status_code == 200
    assert first.json()["status"] == "success"

    second = await client.post(
        "/api/v1/tools/approval", json=payload, headers=user_auth_headers
    )
    assert second.status_code == 409
    assert second.json()["detail"]


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

@pytest.mark.asyncio
async def test_approval_api_hides_foreign_users_tool_call(
    client, db_session, conversation_id
):
    """The HTTP endpoint must not disclose or resolve another user's call."""
    from backend.app.core.security import create_access_token
    from backend.app.domain.user.repository import UserRepository
    from backend.app.domain.user.schemas import UserCreate

    repo = ToolRepository(db_session)
    call = await repo.log_tool_call(
        conversation_id=conversation_id,
        tool_name="web_search",
        input_args={"query": "owner-only"},
        status="requires_approval",
        requires_approval=True,
    )

    foreign_user = await UserRepository(db_session).create(
        UserCreate(
            email=f"foreign-{uuid.uuid4().hex}@example.com",
            username=f"foreign-{uuid.uuid4().hex}",
            password="TestPass123!",
            full_name="Foreign User",
        )
    )
    token = create_access_token(
        foreign_user.id,
        role=foreign_user.role,
        additional_claims={"email": foreign_user.email},
    )
    response = await client.post(
        "/api/v1/tools/approval",
        json={"tool_call_id": str(call.id), "approved": True},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 404
    refreshed = await repo.get_tool_call(call.id)
    assert refreshed.status == "requires_approval"
    assert refreshed.is_approved is False


@pytest.mark.asyncio
async def test_foreign_user_cannot_resolve_pending_tool_call(
    db_session, conversation_id
):
    """The atomic approval resolver must enforce conversation ownership."""
    repo = ToolRepository(db_session)
    call = await repo.log_tool_call(
        conversation_id=conversation_id,
        tool_name="web_search",
        input_args={"query": "private"},
        status="requires_approval",
        requires_approval=True,
    )

    resolved = await repo.resolve_pending_approval(
        tool_call_id=call.id,
        user_id=uuid.uuid4(),
        approved=True,
    )

    assert resolved is False
    refreshed = await repo.get_tool_call(call.id)
    assert refreshed.status == "requires_approval"
    assert refreshed.is_approved is False

