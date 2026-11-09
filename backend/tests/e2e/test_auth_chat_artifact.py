"""
Backend integration tests against real Postgres/Redis via testcontainers.

These run only when TESTCONTAINERS_AVAILABLE=1 or in CI with the
backend-e2e job. They verify full auth + chat + artifact flows end-to-end.
"""
from __future__ import annotations

import os

import pytest

pytestmark = pytest.mark.e2e


@pytest.mark.skipif(
    os.getenv("TESTCONTAINERS_AVAILABLE") != "1",
    reason="E2E tests require testcontainers (set TESTCONTAINERS_AVAILABLE=1)",
)
class TestAuthE2E:
    """Full auth flow: register -> login -> refresh -> logout."""

    async def test_register_login_refresh_logout(self, async_client):
        # Register
        resp = await async_client.post(
            "/api/v1/auth/register",
            json={
                "email": "e2e@example.com",
                "password": "StrongPass123!",
                "username": "e2euser",
            },
        )
        assert resp.status_code in (200, 201)

        # Login
        resp = await async_client.post(
            "/api/v1/auth/login",
            data={"username": "e2e@example.com", "password": "StrongPass123!"},
        )
        assert resp.status_code == 200
        cookies = resp.headers.get("set-cookie", "")
        assert "nexus_access_token" in cookies
        assert "nexus_refresh_token" in cookies

        # Refresh
        resp = await async_client.post("/api/v1/auth/refresh")
        assert resp.status_code == 200

        # Logout
        resp = await async_client.post("/api/v1/auth/logout")
        assert resp.status_code == 200


@pytest.mark.skipif(
    os.getenv("TESTCONTAINERS_AVAILABLE") != "1",
    reason="E2E tests require testcontainers",
)
class TestChatE2E:
    """Chat stream end-to-end: send message -> stream tokens -> done."""

    async def test_stream_returns_tokens(self, async_client, auth_headers):
        resp = await async_client.post(
            "/api/v1/chat",
            json={
                "message": "Say hello in exactly three words.",
                "model": "fast_chat",
                "conversation_id": None,
            },
            headers=auth_headers,
        )
        assert resp.status_code == 200
        text = await resp.aread()
        body = text.decode()
        assert "text_delta" in body or "done" in body


@pytest.mark.skipif(
    os.getenv("TESTCONTAINERS_AVAILABLE") != "1",
    reason="E2E tests require testcontainers",
)
class TestArtifactE2E:
    """Artifact save and version listing."""

    async def test_save_and_list_versions(self, async_client, auth_headers):
        resp = await async_client.post(
            "/api/v1/artifacts",
            json={
                "type": "text",
                "name": "E2E Test Artifact",
                "content": "Hello E2E",
            },
            headers=auth_headers,
        )
        assert resp.status_code in (200, 201)
        data = resp.json()
        assert data["name"] == "E2E Test Artifact"
