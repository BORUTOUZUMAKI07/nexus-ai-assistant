"""
Unit tests for the rate-limit seam — RFC 6585 429 + Retry-After + standard
X-RateLimit-* headers, and the auth-path behaviour when Redis is unreachable.

These test the small HTTP-transport pieces only; they do not need Redis. The
auth path deliberately does *not* fail open any more: an unreachable store now
falls back to this process's own sliding window (``_memory_rate_limit``), so
brute-force protection degrades to per-instance instead of switching off.
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
from fastapi import HTTPException, Request


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
async def test_app_login_allowed_sets_headers(monkeypatch, client):
    """When the limiter allows the request, the standard headers are set and
    the endpoint proceeds (auth failure is a 401, not a 429).

    Uses the shared ``client`` fixture rather than a bare ASGI transport. This
    endpoint looks the user up, so it needs a working database: without the
    fixture's ``get_db`` override the request falls through to whatever
    ``DATABASE_URL`` happens to point at, and on a machine with no local
    Postgres that is a 500 (or a connection error), not the 401 this asserts.
    """
    from backend.app.infrastructure.resilience import rate_limit as rl_mod

    class _AllowingLimiter:
        async def check_rate_limit(self, identifier, limit=100, window_seconds=60, cost=1):
            return True, 59

    monkeypatch.setattr(rl_mod, "redis_service", _AllowingLimiter())

    resp = await client.post(
        "/api/v1/auth/login",
        data={"username": "nobody@example.com", "password": "wrong"},
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )

    assert resp.status_code == 401  # auth fails for bogus creds, but NOT throttled
    assert resp.headers.get(RATE_LIMIT_HEADER_LIMIT) == "60"
    assert resp.headers.get(RATE_LIMIT_HEADER_REMAINING) == "59"
    assert RETRY_AFTER_HEADER not in resp.headers


# ─── Auth path: an unreachable Redis must degrade, not switch off ─────────────


def _auth_request(peer: str = "203.0.113.9") -> Request:
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/v1/auth/login",
            "headers": [],
            "client": (peer, 44444),
            "query_string": b"",
        }
    )


def test_memory_rate_limit_allows_the_limit_then_refuses():
    from backend.app.api.v1 import auth as auth_mod

    key = "unit:test:mem"
    auth_mod._LOCAL_RATE_WINDOWS.pop(key, None)
    try:
        assert [auth_mod._memory_rate_limit(key, limit=3, window_seconds=60) for _ in range(4)] == [
            True,
            True,
            True,
            False,
        ]
    finally:
        auth_mod._LOCAL_RATE_WINDOWS.pop(key, None)


def test_memory_rate_limit_counts_stale_attempts_as_expired():
    """A bucket whose entries are older than the window must not block."""
    import time as _time
    from collections import deque

    from backend.app.api.v1 import auth as auth_mod

    key = "unit:test:stale"
    auth_mod._LOCAL_RATE_WINDOWS[key] = deque([_time.monotonic() - 120])
    try:
        # Without the sweep this would be 1 >= limit and refuse forever.
        assert auth_mod._memory_rate_limit(key, limit=1, window_seconds=60) is True
    finally:
        auth_mod._LOCAL_RATE_WINDOWS.pop(key, None)


@pytest.mark.asyncio
async def test_auth_rate_limit_still_throttles_when_redis_is_unreachable(monkeypatch):
    """The finding: `auth_rate_limit_unavailable` returned early, so an
    unreachable Upstash silently removed the cap on login/register/reset."""
    from backend.app.api.v1 import auth as auth_mod

    async def unreachable_redis(**_kwargs):
        raise ConnectionError("upstash unreachable")

    monkeypatch.setattr(auth_mod.redis_service, "check_rate_limit", unreachable_redis)
    auth_mod._LOCAL_RATE_WINDOWS.clear()
    request = _auth_request()

    try:
        # Within the limit the request must still pass: degraded, not fail-closed.
        for _ in range(5):
            await auth_mod._enforce_auth_rate_limit(
                request, action="login", limit=5, account="victim@example.com"
            )

        with pytest.raises(HTTPException) as exc:
            await auth_mod._enforce_auth_rate_limit(
                request, action="login", limit=5, account="victim@example.com"
            )
        assert exc.value.status_code == 429
        assert exc.value.headers.get("Retry-After") == "60"
    finally:
        auth_mod._LOCAL_RATE_WINDOWS.clear()


@pytest.mark.asyncio
async def test_auth_rate_limit_keeps_a_per_account_bucket_on_the_fallback(monkeypatch):
    """Many source addresses must not outrun one target account when Redis is down."""
    from backend.app.api.v1 import auth as auth_mod

    async def unreachable_redis(**_kwargs):
        raise TimeoutError("upstash unreachable")

    monkeypatch.setattr(auth_mod.redis_service, "check_rate_limit", unreachable_redis)
    auth_mod._LOCAL_RATE_WINDOWS.clear()

    try:
        # One attempt each from four different peers, limit 3. Every *per-IP*
        # bucket is still under its cap, so a 429 here can only come from the
        # per-account bucket — the one that has to survive a Redis outage.
        peers = (
            "198.51.100.1",
            "198.51.100.2",
            "198.51.100.3",
            "198.51.100.4",
        )
        raised_at: int | None = None
        for i, peer in enumerate(peers):
            try:
                await auth_mod._enforce_auth_rate_limit(
                    _auth_request(peer),
                    action="login",
                    limit=3,
                    account="victim@example.com",
                )
            except HTTPException as exc:
                assert exc.status_code == 429
                raised_at = i
                break
        assert raised_at == 3, (
            f"expected the account bucket to fire on the 4th distinct peer, "
            f"got {raised_at}"
        )
    finally:
        auth_mod._LOCAL_RATE_WINDOWS.clear()
