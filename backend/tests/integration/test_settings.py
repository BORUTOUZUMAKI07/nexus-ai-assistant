"""User settings, memories, and BYOK API key endpoints integration tests."""
import uuid

import pytest


@pytest.mark.asyncio
async def test_get_default_settings(client, user_auth_headers):
    resp = await client.get("/api/v1/settings", headers=user_auth_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["theme"] == "dark"
    assert body["default_model"] == "llama-3.3-70b-versatile"
    assert body["enable_memory"] is True


@pytest.mark.asyncio
async def test_update_settings_round_trip_custom_settings(
    client, user_auth_headers
):
    update = await client.put(
        "/api/v1/settings",
        json={
            "theme": "light",
            "temperature": 0.2,
            "custom_settings": {"language": "es", "markers": [1, 2, 3]},
        },
        headers=user_auth_headers,
    )
    assert update.status_code == 200
    body = update.json()
    assert body["theme"] == "light"
    assert body["temperature"] == 0.2
    assert body["custom_settings"] == {"language": "es", "markers": [1, 2, 3]}

    fetched = await client.get("/api/v1/settings", headers=user_auth_headers)
    assert fetched.json()["custom_settings"] == {"language": "es", "markers": [1, 2, 3]}


@pytest.mark.asyncio
async def test_memory_lifecycle(client, user_auth_headers):
    added = await client.post(
        "/api/v1/settings/memories",
        json={"content": "Prefers terse answers", "category": "preference"},
        headers=user_auth_headers,
    )
    assert added.status_code == 201
    memory_id = added.json()["id"]
    assert added.json()["is_active"] is True

    listing = await client.get("/api/v1/settings/memories", headers=user_auth_headers)
    assert [m["id"] for m in listing.json()] == [memory_id]

    deleted = await client.delete(
        f"/api/v1/settings/memories/{memory_id}", headers=user_auth_headers
    )
    assert deleted.status_code == 204

    after = await client.get("/api/v1/settings/memories", headers=user_auth_headers)
    assert after.json() == []


@pytest.mark.asyncio
async def test_delete_unknown_memory_404(client, user_auth_headers):
    resp = await client.delete(
        f"/api/v1/settings/memories/{uuid.uuid4()}", headers=user_auth_headers
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_api_key_save_and_preview(client, user_auth_headers):
    saved = await client.post(
        "/api/v1/settings/keys",
        json={"provider": "openai", "key_value": "sk-test-abcdef123456"},
        headers=user_auth_headers,
    )
    assert saved.status_code == 201
    body = saved.json()
    assert body["key_preview"] == "...3456"
    assert "sk-test" not in body["key_preview"]

    listing = await client.get("/api/v1/settings/keys", headers=user_auth_headers)
    assert len(listing.json()) == 1


@pytest.mark.asyncio
async def test_api_key_upsert_same_provider(client, user_auth_headers):
    await client.post(
        "/api/v1/settings/keys",
        json={"provider": "groq", "key_value": "gsk-firstsecret"},
        headers=user_auth_headers,
    )
    second = await client.post(
        "/api/v1/settings/keys",
        json={"provider": "groq", "key_value": "gsk-secondsupersecret"},
        headers=user_auth_headers,
    )
    assert second.status_code == 201
    assert second.json()["key_preview"] == "...cret"

    listing = await client.get("/api/v1/settings/keys", headers=user_auth_headers)
    keys = listing.json()
    assert len(keys) == 1
    assert keys[0]["provider"] == "groq"
    assert keys[0]["key_preview"] == "...cret"


@pytest.mark.asyncio
async def test_settings_requires_auth(client):
    assert (await client.get("/api/v1/settings")).status_code == 401


@pytest.mark.asyncio
async def test_memory_listing_pagination_and_bounds(client, user_auth_headers):
    for content in ("memory one", "memory two"):
        created = await client.post(
            "/api/v1/settings/memories",
            json={"content": content, "category": "preference"},
            headers=user_auth_headers,
        )
        assert created.status_code == 201

    first_page = await client.get(
        "/api/v1/settings/memories?limit=1&offset=0", headers=user_auth_headers
    )
    second_page = await client.get(
        "/api/v1/settings/memories?limit=1&offset=1", headers=user_auth_headers
    )
    assert first_page.status_code == second_page.status_code == 200
    assert len(first_page.json()) == len(second_page.json()) == 1
    assert first_page.json()[0]["id"] != second_page.json()[0]["id"]

    assert (await client.get(
        "/api/v1/settings/memories?limit=0", headers=user_auth_headers
    )).status_code == 422
    assert (await client.get(
        "/api/v1/settings/memories?limit=101", headers=user_auth_headers
    )).status_code == 422
    assert (await client.get(
        "/api/v1/settings/memories?offset=-1", headers=user_auth_headers
    )).status_code == 422


@pytest.mark.asyncio
async def test_api_key_delete_round_trip_and_can_readd(client, user_auth_headers):
    saved = await client.post(
        "/api/v1/settings/keys",
        json={"provider": "openai", "key_value": "sk-delete-me-123456"},
        headers=user_auth_headers,
    )
    assert saved.status_code == 201
    key_id = saved.json()["id"]

    deleted = await client.delete(
        f"/api/v1/settings/keys/{key_id}", headers=user_auth_headers
    )
    assert deleted.status_code == 204

    listing = await client.get("/api/v1/settings/keys", headers=user_auth_headers)
    assert listing.json() == []

    # A hard delete, not the soft `is_active = False` the memory path uses:
    # `uq_api_key_user_provider` is unconditional, so a surviving inactive row
    # would make re-adding the same provider INSERT into a duplicate and 500.
    readded = await client.post(
        "/api/v1/settings/keys",
        json={"provider": "openai", "key_value": "sk-readd-abcdef"},
        headers=user_auth_headers,
    )
    assert readded.status_code == 201
    assert readded.json()["key_preview"] == "...cdef"


@pytest.mark.asyncio
async def test_delete_unknown_api_key_404(client, user_auth_headers):
    resp = await client.delete(
        f"/api/v1/settings/keys/{uuid.uuid4()}", headers=user_auth_headers
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_cannot_delete_another_users_api_key(client, user_auth_headers, db_session):
    from backend.app.domain.user.models import APIKey, User
    from sqlmodel import select

    other = User(
        email=f"other-{uuid.uuid4().hex[:8]}@example.com",
        username=f"other-{uuid.uuid4().hex[:8]}",
        hashed_password="not-a-real-hash",
        role="user",
    )
    db_session.add(other)
    await db_session.commit()
    await db_session.refresh(other)

    foreign = APIKey(
        user_id=other.id,
        provider="groq",
        encrypted_key="enc",
        key_preview="...zzzz",
    )
    db_session.add(foreign)
    await db_session.commit()
    await db_session.refresh(foreign)
    foreign_id = foreign.id

    resp = await client.delete(
        f"/api/v1/settings/keys/{foreign_id}", headers=user_auth_headers
    )
    assert resp.status_code == 404

    # The 404 must be a refusal, not a silently-successful deletion. Read past
    # this session's identity map so the assertion reflects the database.
    db_session.expire_all()
    survivor = (
        await db_session.exec(select(APIKey).where(APIKey.id == foreign_id))
    ).first()
    assert survivor is not None


@pytest.mark.asyncio
async def test_api_key_listing_pagination_and_bounds(client, user_auth_headers):
    for provider, secret in (("openai", "sk-first-secret"), ("groq", "gsk-second-secret")):
        created = await client.post(
            "/api/v1/settings/keys",
            json={"provider": provider, "key_value": secret},
            headers=user_auth_headers,
        )
        assert created.status_code == 201

    first_page = await client.get(
        "/api/v1/settings/keys?limit=1&offset=0", headers=user_auth_headers
    )
    second_page = await client.get(
        "/api/v1/settings/keys?limit=1&offset=1", headers=user_auth_headers
    )
    assert first_page.status_code == second_page.status_code == 200
    assert len(first_page.json()) == len(second_page.json()) == 1
    assert first_page.json()[0]["provider"] != second_page.json()[0]["provider"]

    assert (await client.get(
        "/api/v1/settings/keys?limit=0", headers=user_auth_headers
    )).status_code == 422
    assert (await client.get(
        "/api/v1/settings/keys?limit=101", headers=user_auth_headers
    )).status_code == 422
    assert (await client.get(
        "/api/v1/settings/keys?offset=-1", headers=user_auth_headers
    )).status_code == 422
