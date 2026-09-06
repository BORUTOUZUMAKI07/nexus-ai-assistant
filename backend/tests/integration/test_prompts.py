"""Prompt templates and skills endpoints integration tests."""
import uuid

import pytest


def _register(client, prefix):
    suffix = uuid.uuid4().hex[:8]
    payload = {
        "email": f"{prefix}-{suffix}@example.com",
        "username": f"{prefix}-{suffix}",
        "password": "StrongPass123!",
    }
    return {"payload": payload, "suffix": suffix}


async def _headers_for(client, payload):
    login = await client.post(
        "/api/v1/auth/login",
        data={"username": payload["email"], "password": payload["password"]},
    )
    assert login.status_code == 200
    return {"Authorization": f"Bearer {login.json()['access_token']}"}


@pytest.mark.asyncio
async def test_create_and_list_own_template(client, user_auth_headers):
    created = await client.post(
        "/api/v1/prompts/templates",
        json={
            "title": "Code Reviewer",
            "category": "engineering",
            "system_prompt": "You are a meticulous code reviewer.",
            "input_variables": ["language"],
        },
        headers=user_auth_headers,
    )
    assert created.status_code == 201
    body = created.json()
    assert body["version"] == 1
    assert body["input_variables"] == ["language"]

    listing = await client.get("/api/v1/prompts/templates", headers=user_auth_headers)
    assert listing.status_code == 200
    assert [t["title"] for t in listing.json()] == ["Code Reviewer"]


@pytest.mark.asyncio
async def test_public_templates_are_shared_private_are_not(
    client, user_auth_headers
):
    own = await client.post(
        "/api/v1/prompts/templates",
        json={"title": "Mine", "system_prompt": "My private boilerplate."},
        headers=user_auth_headers,
    )
    assert own.status_code == 201

    other = _register(client, "other")
    await client.post(
        "/api/v1/auth/register",
        json=other["payload"],
    )
    other_headers = await _headers_for(client, other["payload"])
    public = await client.post(
        "/api/v1/prompts/templates",
        json={
            "title": "Shared",
            "system_prompt": "Publicly reusable instructions.",
            "is_public": True,
        },
        headers=other_headers,
    )
    assert public.status_code == 201

    third = _register(client, "third")
    await client.post(
        "/api/v1/auth/register",
        json=third["payload"],
    )
    third_headers = await _headers_for(client, third["payload"])
    await client.post(
        "/api/v1/prompts/templates",
        json={"title": "Hidden", "system_prompt": "Do not share this."},
        headers=third_headers,
    )

    listing = await client.get("/api/v1/prompts/templates", headers=user_auth_headers)
    titles = [t["title"] for t in listing.json()]
    assert "Mine" in titles
    assert "Shared" in titles
    assert "Hidden" not in titles


@pytest.mark.asyncio
async def test_list_skills_returns_list(client, user_auth_headers):
    resp = await client.get("/api/v1/prompts/skills", headers=user_auth_headers)
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)
