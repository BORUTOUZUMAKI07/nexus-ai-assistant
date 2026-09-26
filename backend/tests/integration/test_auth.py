"""Auth endpoints integration tests: register, login, refresh, me, guards."""
import uuid

import pytest
from backend.app.domain.user.repository import UserRepository


@pytest.mark.asyncio
async def test_register_creates_user(client):
    email = f"reg-{uuid.uuid4().hex[:8]}@example.com"
    resp = await client.post(
        "/api/v1/auth/register",
        json={
            "email": email,
            "username": f"reguser-{uuid.uuid4().hex[:6]}",
            "password": "StrongPass123!",
            "full_name": "Registered User",
        },
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["email"] == email
    assert body["role"] == "user"
    assert body["is_active"] is True
    assert "hashed_password" not in body


@pytest.mark.asyncio
async def test_register_duplicate_email_conflicts(client):
    payload = {
        "email": f"dup-{uuid.uuid4().hex[:8]}@example.com",
        "username": f"dupuser-{uuid.uuid4().hex[:6]}",
        "password": "StrongPass123!",
    }
    first = await client.post("/api/v1/auth/register", json=payload)
    assert first.status_code == 201

    second = await client.post("/api/v1/auth/register", json=payload)
    assert second.status_code == 409


@pytest.mark.asyncio
async def test_register_weak_password_rejected(client):
    resp = await client.post(
        "/api/v1/auth/register",
        json={
            "email": f"weak-{uuid.uuid4().hex[:8]}@example.com",
            "username": "weakuser",
            "password": "short",
        },
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_login_and_me_flow(client):
    email = f"login-{uuid.uuid4().hex[:8]}@example.com"
    password = "StrongPass123!"
    await client.post(
        "/api/v1/auth/register",
        json={
            "email": email,
            "username": f"login-{uuid.uuid4().hex[:6]}",
            "password": password,
        },
    )

    resp = await client.post(
        "/api/v1/auth/login",
        data={"username": email, "password": password},
    )
    assert resp.status_code == 200
    tokens = resp.json()
    assert tokens["token_type"] == "bearer"
    assert tokens["access_token"]
    assert tokens["refresh_token"]

    headers = {"Authorization": f"Bearer {tokens['access_token']}"}
    me = await client.get("/api/v1/auth/me", headers=headers)
    assert me.status_code == 200
    assert me.json()["email"] == email


@pytest.mark.asyncio
async def test_login_wrong_password_401(client):
    email = f"badpw-{uuid.uuid4().hex[:8]}@example.com"
    await client.post(
        "/api/v1/auth/register",
        json={
            "email": email,
            "username": f"badpw-{uuid.uuid4().hex[:6]}",
            "password": "StrongPass123!",
        },
    )
    resp = await client.post(
        "/api/v1/auth/login",
        data={"username": email, "password": "WrongPass123!"},
    )
    assert resp.status_code == 401
    assert resp.headers.get("www-authenticate")


@pytest.mark.asyncio
async def test_login_inactive_user_401(client, db_session):
    suffix = uuid.uuid4().hex[:8]
    email = f"inactive-{suffix}@example.com"
    await client.post(
        "/api/v1/auth/register",
        json={
            "email": email,
            "username": f"inactive-{suffix}",
            "password": "StrongPass123!",
        },
    )

    repo = UserRepository(db_session)
    user = await repo.get_by_email(email)
    assert user is not None
    user.is_active = False
    db_session.add(user)
    await db_session.commit()

    resp = await client.post(
        "/api/v1/auth/login",
        data={"username": email, "password": "StrongPass123!"},
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_refresh_token_rotates(client, redis_backend):
    email = f"refresh-{uuid.uuid4().hex[:8]}@example.com"
    password = "StrongPass123!"
    await client.post(
        "/api/v1/auth/register",
        json={
            "email": email,
            "username": f"refresh-{uuid.uuid4().hex[:6]}",
            "password": password,
        },
    )
    login = await client.post(
        "/api/v1/auth/login",
        data={"username": email, "password": password},
    )
    refresh_token = login.json()["refresh_token"]

    resp = await client.post(
        "/api/v1/auth/refresh",
        json={"refresh_token": refresh_token},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["access_token"]
    assert body["refresh_token"]


@pytest.mark.asyncio
async def test_refresh_invalid_token_401(client):
    resp = await client.post(
        "/api/v1/auth/refresh",
        json={"refresh_token": "not.a.valid.token"},
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_refresh_token_is_single_use(client, redis_backend):
    email = f"reuse-{uuid.uuid4().hex[:8]}@example.com"
    password = "StrongPass123!"
    await client.post(
        "/api/v1/auth/register",
        json={
            "email": email,
            "username": f"reuse-{uuid.uuid4().hex[:6]}",
            "password": password,
        },
    )
    login = await client.post(
        "/api/v1/auth/login",
        data={"username": email, "password": password},
    )
    refresh_token = login.json()["refresh_token"]

    first = await client.post(
        "/api/v1/auth/refresh",
        json={"refresh_token": refresh_token},
    )
    assert first.status_code == 200

    # Replaying the same refresh token must be refused (rotation + reuse guard).
    replay = await client.post(
        "/api/v1/auth/refresh",
        json={"refresh_token": refresh_token},
    )
    assert replay.status_code == 401


@pytest.mark.asyncio
async def test_refresh_token_not_usable_as_access(client):
    email = f"scope-{uuid.uuid4().hex[:8]}@example.com"
    password = "StrongPass123!"
    await client.post(
        "/api/v1/auth/register",
        json={
            "email": email,
            "username": f"scope-{uuid.uuid4().hex[:6]}",
            "password": password,
        },
    )
    login = await client.post(
        "/api/v1/auth/login",
        data={"username": email, "password": password},
    )
    refresh_token = login.json()["refresh_token"]

    resp = await client.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {refresh_token}"},
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_protected_route_requires_token(client):
    resp = await client.get("/api/v1/conversations")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_me_requires_valid_token(client, user_auth_headers):
    resp = await client.get("/api/v1/auth/me", headers=user_auth_headers)
    assert resp.status_code == 200
    assert resp.json()["id"] is not None



@pytest.mark.asyncio
@pytest.mark.parametrize("endpoint,limit", [
    ("/api/v1/auth/login", 10),
    ("/api/v1/auth/register", 5),
])
async def test_public_auth_routes_return_429_when_rate_limited(
    client, monkeypatch, endpoint, limit
):
    from backend.app.infrastructure.cache.redis_client import redis_service

    calls = []

    async def deny_request(**kwargs):
        calls.append(kwargs)
        return False, 0

    monkeypatch.setattr(redis_service, "check_rate_limit", deny_request)
    if endpoint.endswith("/login"):
        response = await client.post(
            endpoint,
            data={"username": "someone@example.com", "password": "StrongPass123!"},
        )
    else:
        response = await client.post(
            endpoint,
            json={
                "email": f"limited-{uuid.uuid4().hex[:8]}@example.com",
                "username": f"limited-{uuid.uuid4().hex[:8]}",
                "password": "StrongPass123!",
            },
        )

    assert response.status_code == 429
    assert response.headers["retry-after"] == "60"
    assert len(calls) == 1
    assert calls[0]["limit"] == limit
    assert calls[0]["window_seconds"] == 60



@pytest.mark.asyncio
async def test_refresh_route_returns_429_when_rate_limited(client, monkeypatch):
    from backend.app.infrastructure.cache.redis_client import redis_service

    calls = []

    async def deny_request(**kwargs):
        calls.append(kwargs)
        return False, 0

    monkeypatch.setattr(redis_service, "check_rate_limit", deny_request)
    response = await client.post(
        "/api/v1/auth/refresh",
        json={"refresh_token": "syntactically.valid.token"},
    )

    assert response.status_code == 429
    assert response.headers["retry-after"] == "60"
    assert len(calls) == 1
    assert calls[0]["identifier"].startswith("auth:refresh:")
    assert calls[0]["limit"] == 20


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    [
        {"refresh_token": ""},
        {"refresh_token": "x" * 4097},
        {"refresh_token": "token", "unexpected": "field"},
    ],
)
async def test_refresh_rejects_invalid_payload_shape(client, payload):
    response = await client.post("/api/v1/auth/refresh", json=payload)
    assert response.status_code == 422
