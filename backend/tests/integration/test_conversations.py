"""Conversations endpoints integration tests: CRUD, archive, ownership, fork."""
import uuid

import pytest
from backend.app.domain.conversation.models import Message
from backend.app.domain.conversation.repository import ConversationRepository
from backend.app.domain.file.models import File


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
async def test_delete_conversation_with_messages_and_files(
    client, user_auth_headers, test_user, session_factory
):
    """Deleting a conversation cascades messages + conversation-linked files."""
    from backend.app.domain.file.repository import FileRepository

    created = await client.post(
        "/api/v1/conversations", json={"title": "Has data"}, headers=user_auth_headers
    )
    conv_id = created.json()["id"]

    async with session_factory() as session:
        repo = ConversationRepository(session)
        m1 = await repo.add_message(conv_id, "user", "what is DP")
        await repo.add_message(conv_id, "assistant", "dynamic programming", parent_message_id=m1.id)
        await FileRepository(session).create_file(
            user_id=test_user.id,
            filename="a.pdf",
            original_filename="a.pdf",
            file_type="pdf",
            mime_type="application/pdf",
            size_bytes=10,
            storage_path="fake/a.pdf",
            conversation_id=conv_id,
        )

    deleted = await client.delete(
        f"/api/v1/conversations/{conv_id}", headers=user_auth_headers
    )
    assert deleted.status_code == 204

    gone = await client.get(f"/api/v1/conversations/{conv_id}", headers=user_auth_headers)
    assert gone.status_code == 404

    async with session_factory() as session:
        from sqlmodel import select

        leftover_messages = (await session.exec(
            select(Message).where(Message.conversation_id == conv_id)
        )).all()
        leftover_files = (await session.exec(
            select(File).where(File.conversation_id == conv_id)
        )).all()
    assert leftover_messages == []
    assert leftover_files == []


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


@pytest.mark.asyncio
@pytest.mark.parametrize("query", ["?limit=0", "?limit=101", "?offset=-1", "?offset=1000001"])
async def test_conversation_pagination_rejects_out_of_bounds(client, user_auth_headers, query):
    response = await client.get(f"/api/v1/conversations{query}", headers=user_auth_headers)
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_conversation_create_rejects_unknown_fields(client, user_auth_headers):
    response = await client.post(
        "/api/v1/conversations",
        json={"title": "Bounded", "unexpected_admin_flag": True},
        headers=user_auth_headers,
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_conversation_title_length_is_bounded(client, user_auth_headers):
    response = await client.post(
        "/api/v1/conversations",
        json={"title": "x" * 201},
        headers=user_auth_headers,
    )
    assert response.status_code == 422
