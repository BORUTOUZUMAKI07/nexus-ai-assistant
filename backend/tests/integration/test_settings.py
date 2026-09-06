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
