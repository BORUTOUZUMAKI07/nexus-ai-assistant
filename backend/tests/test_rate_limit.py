"""
Unit tests for the rate-limit seam — RFC 6585 429 + Retry-After + standard
X-RateLimit-* headers, fail-open on Redis outage.

These test the small HTTP-transport pieces only; they do not need Redis (the
seam fails open when the store is unreachable).
"""
from __future__ import annotations

import json

import pytest
from backend.app.infrastructure.resilience.rate_limit import (
    RATE_LIMIT_HEADER_LIMIT,
    RATE_LIMIT_HEADER_REMAINING,
    RATE_LIMIT_HEADER_RESET,
    RETRY_AFTER_HEADER,
    RateLimitError,
    _build_header_payload,
    rate_limit_error_handler,
)
from fastapi import Request


def test_server_side_header_payload_is_standard():
    headers = _build_header_payload(limit=100, remaining=37, window_seconds=60)
    assert headers == {
        RATE_LIMIT_HEADER_LIMIT: "100",
        RATE_LIMIT_HEADER_REMAINING: "37",
        RATE_LIMIT_HEADER_RESET: "60",
    }


def test_header_payload_clamps_negative_remaining():
    headers = _build_header_payload(limit=5, remaining=-2, window_seconds=60)
    assert headers[RATE_LIMIT_HEADER_REMAINING] == "0"


def test_rate_limit_exceeded_carries_retry_after():
    exc = RateLimitError(retry_after_seconds=15)
    assert exc.retry_after_seconds == 15
    assert "15" in str(exc)


@pytest.mark.asyncio
async def test_rate_limit_exceeded_handler_is_rfc6585():
    request = Request({"type": "http", "method": "GET", "path": "/x", "headers": []})
    response = rate_limit_error_handler(request, RateLimitError(retry_after_seconds=30))

    assert response.status_code == 429
    assert response.headers[RETRY_AFTER_HEADER] == "30"
    body = response.body.decode()
    assert "RATE_LIMIT_EXCEEDED" in body
    assert "rate-limit-exceeded" in body
    assert json.loads(body)["status"] == 429


@pytest.mark.asyncio
async def test_rate_limit_exceeded_carries_full_standard_header_payload():
    exc = RateLimitError(
        retry_after_seconds=60, limit=60, remaining=0, window_seconds=60
    )
    assert exc.headers == {
        RETRY_AFTER_HEADER: "60",
        RATE_LIMIT_HEADER_LIMIT: "60",
        RATE_LIMIT_HEADER_REMAINING: "0",
        RATE_LIMIT_HEADER_RESET: "60",
    }


@pytest.mark.asyncio
async def test_app_login_returns_429_with_retry_after_when_limit_hit(monkeypatch):
    """End-to-end through the ASGI app: when the (fake) limiter denies, the
    client sees 429 + Retry-After + the standard rate-limit headers."""
    from backend.app.infrastructure.resilience import rate_limit as rl_mod
    from backend.app.main import app
    from httpx import ASGITransport, AsyncClient

    class _DenyingLimiter:
        async def check_rate_limit(self, identifier, limit=100, window_seconds=60, cost=1):
            return False, 0  # denied, zero remaining

    monkeypatch.setattr(rl_mod, "redis_service", _DenyingLimiter())

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.post(
            "/api/v1/auth/login",
            data={"username": "a@b.c", "password": "whatever"},
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )

    assert resp.status_code == 429
    assert resp.headers.get(RETRY_AFTER_HEADER) == "60"
    assert resp.headers.get(RATE_LIMIT_HEADER_LIMIT) == "60"
    assert resp.headers.get(RATE_LIMIT_HEADER_REMAINING) == "0"
    assert "rate-limit-exceeded" in resp.text


@pytest.mark.asyncio
async def test_app_login_allowed_sets_headers(monkeypatch):
    """When the limiter allows the request, the standard headers are set and
    the endpoint proceeds (auth failure is a 401, not a 429)."""
    from backend.app.infrastructure.resilience import rate_limit as rl_mod
    from backend.app.main import app
    from httpx import ASGITransport, AsyncClient

    class _AllowingLimiter:
        async def check_rate_limit(self, identifier, limit=100, window_seconds=60, cost=1):
            return True, 59

    monkeypatch.setattr(rl_mod, "redis_service", _AllowingLimiter())

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.post(
            "/api/v1/auth/login",
            data={"username": "nobody@example.com", "password": "wrong"},
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )

    assert resp.status_code == 401  # auth fails for bogus creds, but NOT throttled
    assert resp.headers.get(RATE_LIMIT_HEADER_LIMIT) == "60"
    assert resp.headers.get(RATE_LIMIT_HEADER_REMAINING) == "59"
    assert RETRY_AFTER_HEADER not in resp.headers
