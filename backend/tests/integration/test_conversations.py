"""Conversations endpoints integration tests: CRUD, archive, ownership, fork."""
import uuid

import pytest
from backend.app.domain.conversation.repository import ConversationRepository


@pytest.mark.asyncio
async def test_create_and_list_conversation(client, user_auth_headers):
    created = await client.post(
        "/api/v1/conversations",
        json={"title": "Integration Chat", "model": "llama-3.3-70b-versatile"},
        headers=user_auth_headers,
    )
    assert created.status_code == 201
    body = created.json()
    assert body["title"] == "Integration Chat"
    assert body["is_archived"] is False

    listing = await client.get("/api/v1/conversations", headers=user_auth_headers)
    assert listing.status_code == 200
    assert any(c["id"] == body["id"] for c in listing.json())


@pytest.mark.asyncio
async def test_get_conversation_detail(client, user_auth_headers):
    created = await client.post(
        "/api/v1/conversations",
        json={"title": "Detail Chat"},
        headers=user_auth_headers,
    )
    conv_id = created.json()["id"]

    detail = await client.get(f"/api/v1/conversations/{conv_id}", headers=user_auth_headers)
    assert detail.status_code == 200
    assert detail.json()["messages"] == []


@pytest.mark.asyncio
async def test_update_conversation_fields(client, user_auth_headers):
    created = await client.post(
        "/api/v1/conversations", json={"title": "Before"}, headers=user_auth_headers
    )
    conv_id = created.json()["id"]

    resp = await client.patch(
        f"/api/v1/conversations/{conv_id}",
        json={"title": "After", "is_pinned": True, "system_prompt": "Be terse."},
        headers=user_auth_headers,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["title"] == "After"
    assert body["is_pinned"] is True


@pytest.mark.asyncio
async def test_archive_excludes_from_default_list(client, user_auth_headers):
    created = await client.post(
        "/api/v1/conversations", json={"title": "Archive me"}, headers=user_auth_headers
    )
    conv_id = created.json()["id"]
    await client.patch(
        f"/api/v1/conversations/{conv_id}",
        json={"is_archived": True},
        headers=user_auth_headers,
    )

    active = await client.get("/api/v1/conversations", headers=user_auth_headers)
    assert all(c["id"] != conv_id for c in active.json())

    archived = await client.get(
        "/api/v1/conversations?archived=true", headers=user_auth_headers
    )
    assert any(c["id"] == conv_id for c in archived.json())


@pytest.mark.asyncio
async def test_delete_conversation(client, user_auth_headers):
    created = await client.post(
        "/api/v1/conversations", json={"title": "Doomed"}, headers=user_auth_headers
    )
    conv_id = created.json()["id"]

    deleted = await client.delete(
        f"/api/v1/conversations/{conv_id}", headers=user_auth_headers
    )
    assert deleted.status_code == 204

    gone = await client.get(f"/api/v1/conversations/{conv_id}", headers=user_auth_headers)
    assert gone.status_code == 404


@pytest.mark.asyncio
async def test_cannot_access_other_users_conversation(client, user_auth_headers):
    created = await client.post(
        "/api/v1/conversations", json={"title": "Private"}, headers=user_auth_headers
    )
    conv_id = created.json()["id"]

    other_suffix = uuid.uuid4().hex[:8]
    await client.post(
        "/api/v1/auth/register",
        json={
            "email": f"other-{other_suffix}@example.com",
            "username": f"other-{other_suffix}",
            "password": "StrongPass123!",
        },
    )
    login = await client.post(
        "/api/v1/auth/login",
        data={
            "username": f"other-{other_suffix}@example.com",
            "password": "StrongPass123!",
        },
    )
    other_headers = {
        "Authorization": f"Bearer {login.json()['access_token']}"
    }

    forbidden = await client.get(
        f"/api/v1/conversations/{conv_id}", headers=other_headers
    )
    assert forbidden.status_code == 404

    listing = await client.get("/api/v1/conversations", headers=other_headers)
    assert all(c["id"] != conv_id for c in listing.json())


@pytest.mark.asyncio
async def test_invalid_uuid_returns_422(client, user_auth_headers):
    resp = await client.get("/api/v1/conversations/not-a-uuid", headers=user_auth_headers)
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_fork_copies_messages_up_to_fork_point(
    client, user_auth_headers, db_session, test_user
):
    repo = ConversationRepository(db_session)
    parent = await repo.create(
        user_id=test_user.id,
        title="Parent chat",
    )
    first = await repo.add_message(parent.id, "user", "first question")
    second = await repo.add_message(
        parent.id, "assistant", "first answer", parent_message_id=first.id
    )
    await repo.add_message(parent.id, "user", "second question", parent_message_id=second.id)

    resp = await client.post(
        f"/api/v1/conversations/{parent.id}/fork",
        json={"fork_message_id": str(second.id), "branch_name": "experiment"},
        headers=user_auth_headers,
    )
    assert resp.status_code == 200
    branch = resp.json()
    assert branch["id"] != parent.id

    detail = await client.get(
        f"/api/v1/conversations/{branch['id']}", headers=user_auth_headers
    )
    messages = detail.json()["messages"]
    assert len(messages) == 2
    assert messages[0]["role"] == "user"
    assert messages[1]["role"] == "assistant"
