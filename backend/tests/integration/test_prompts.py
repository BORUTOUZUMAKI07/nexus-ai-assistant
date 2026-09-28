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


async def _registered(client, prefix):
    """Register a user and return its auth headers.

    The register response is asserted on purpose. Ignoring it turns a
    throttled or rejected registration into a confusing 401 from the login
    that follows, which points at the wrong thing entirely.
    """
    created = _register(client, prefix)
    response = await client.post("/api/v1/auth/register", json=created["payload"])
    assert response.status_code == 201, f"register {prefix} -> {response.status_code} {response.text}"
    return await _headers_for(client, created["payload"])


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
    # Not `== ["Code Reviewer"]`: this listing returns the caller's own templates
    # *plus every public template* (see test_public_templates_are_shared_private_are_not),
    # and the suite shares one database, so another test's public template is
    # legitimately in the result depending on the order tests run in. Comparing
    # the whole list for equality made this test pass only when it happened to run
    # first. Assert the property the test is named for instead: the template just
    # created is listed once, with the fields it was stored with.
    rows = [t for t in listing.json() if t["title"] == "Code Reviewer"]
    assert len(rows) == 1, f"expected exactly one 'Code Reviewer', got {listing.json()}"
    assert rows[0]["version"] == 1
    assert rows[0]["category"] == "engineering"
    assert rows[0]["input_variables"] == ["language"]


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

    other_headers = await _registered(client, "other")
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

    third_headers = await _registered(client, "third")
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


@pytest.mark.asyncio
async def test_prompt_template_rejects_unknown_fields(client, user_auth_headers):
    response = await client.post(
        "/api/v1/prompts/templates",
        json={
            "title": "Strict schema",
            "system_prompt": "A sufficiently long system prompt.",
            "unexpected": "not allowed",
        },
        headers=user_auth_headers,
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_prompt_template_bounds_system_prompt(client, user_auth_headers):
    response = await client.post(
        "/api/v1/prompts/templates",
        json={"title": "Long prompt", "system_prompt": "x" * 20001},
        headers=user_auth_headers,
    )
    assert response.status_code == 422
