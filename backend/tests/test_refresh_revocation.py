"""
Refresh-token family revocation.

A refresh token is single-use, so presenting one twice is by definition a
replay — either the token leaked, or two parties hold the same credential.
Either way the app cannot tell the legitimate session from the attacker, so the
only safe response is to cut *every* refresh token for that user and force a
fresh sign-in. The same reasoning applies to a password reset: it is a
credential change, so sessions minted under the old credential must not survive
it.

These tests exercise the marker through the ``ICacheService`` seam (a fake,
never a real Redis) and assert the two properties that matter:

  1. a replay revokes the whole family, not just the replayed token;
  2. an unrelated, never-replayed token for the same user is also refused
     afterwards — that is the whole point of "family" revocation.
"""
import asyncio
from datetime import timedelta
from uuid import uuid4

import pytest
from backend.app.core.exceptions import AuthenticationError
from backend.app.core.security import create_access_token, create_refresh_token
from backend.app.domain.user.models import User
from backend.app.domain.user.schemas import TokenResponse
from backend.app.services.auth_service import AuthService
from backend.tests.fakes import FakeSession


class MemoryCache:
    """In-memory stand-in for the Redis-backed ``ICacheService``.

    Only the three primitives AuthService.refresh uses are implemented, which
    keeps the test honest about the interface surface the service depends on.
    """

    def __init__(self) -> None:
        self.store: dict[str, str] = {}
        self.sets: list[tuple[str, int | None]] = []

    async def get(self, key: str) -> str | None:
        return self.store.get(key)

    async def set(self, key: str, value: str, ttl_seconds: int | None = None) -> bool:
        self.store[key] = value
        self.sets.append((key, ttl_seconds))
        return True

    async def set_if_absent(self, key: str, value: str, ttl_seconds: int) -> bool:
        if key in self.store:
            return False
        self.store[key] = value
        self.sets.append((key, ttl_seconds))
        return True


def _await(coro):
    # Sync test driving a coroutine directly. pytest-asyncio tears the current
    # loop down after each async test, so re-establish one instead of depending
    # on collection order.
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    return loop.run_until_complete(coro)


@pytest.fixture
def cache(monkeypatch):
    """Point AuthService at an in-memory cache for the duration of a test."""
    fake = MemoryCache()
    monkeypatch.setattr(
        "backend.app.services.auth_service.get_cache_service", lambda: fake
    )
    return fake


def _seeded_service() -> tuple[FakeSession, AuthService, User]:
    fake = FakeSession()
    user = User(
        email=f"refresh-{uuid4().hex[:8]}@nexus.ai",
        username=f"user{uuid4().hex[:6]}",
        is_active=True,
        is_verified=True,
    )
    fake.seed(User, [user])
    return fake, AuthService(fake), user


def test_refresh_issues_rotated_pair(cache):
    _fake, svc, user = _seeded_service()

    result = _await(svc.refresh(create_refresh_token(subject=user.id)))

    assert isinstance(result, TokenResponse)
    assert result.access_token and result.refresh_token
    # The cookie TTL the frontend applies must come from the backend, and must
    # describe the refresh token (not the access token).
    assert result.refresh_expires_in == 30 * 86400


def test_replayed_refresh_token_revokes_the_whole_family(cache):
    """Replaying a token must lock out the user's *other* sessions too.

    Token B was never replayed — it is a perfectly legitimate session on another
    device. It must still die, because the app cannot tell it apart from an
    attacker's copy.
    """
    _fake, svc, user = _seeded_service()
    token_a = create_refresh_token(subject=user.id)
    token_b = create_refresh_token(subject=user.id)

    assert _await(svc.refresh(token_a))  # first use is fine

    with pytest.raises(AuthenticationError, match="already been used"):
        _await(svc.refresh(token_a))  # replay

    # The collateral damage is the point: the innocent session dies too.
    with pytest.raises(AuthenticationError, match="revoked"):
        _await(svc.refresh(token_b))


def test_revocation_marker_is_written_with_a_refresh_lifetime(cache):
    """The marker must expire with the token it invalidates, not outlive it."""
    _fake, svc, user = _seeded_service()
    token = create_refresh_token(subject=user.id)
    _await(svc.refresh(token))
    with pytest.raises(AuthenticationError):
        _await(svc.refresh(token))

    marker = f"refresh:revoked:{user.id}"
    assert marker in cache.store
    assert (marker, 30 * 86400) in cache.sets


def test_family_revocation_can_be_disabled(cache, monkeypatch):
    """REFRESH_REVOKE_ON_REUSE=False restores per-token-only behaviour."""
    monkeypatch.setattr(
        "backend.app.services.auth_service.settings.REFRESH_REVOKE_ON_REUSE", False
    )
    _fake, svc, user = _seeded_service()
    token_a = create_refresh_token(subject=user.id)
    token_b = create_refresh_token(subject=user.id)

    _await(svc.refresh(token_a))
    with pytest.raises(AuthenticationError, match="already been used"):
        _await(svc.refresh(token_a))

    # Sibling session survives when cascading is turned off.
    assert _await(svc.refresh(token_b))
    assert f"refresh:revoked:{user.id}" not in cache.store


def test_password_reset_revokes_existing_sessions(cache):
    """A reset is a credential change: pre-reset sessions must not survive it."""
    _fake, svc, user = _seeded_service()
    live_token = create_refresh_token(subject=user.id)
    _await(svc.refresh(live_token))

    reset_token = create_access_token(
        subject=user.id,
        expires_delta=timedelta(minutes=30),
        token_type="reset_password",
    )
    _await(svc.reset_password(reset_token, "BrandNewPass123!"))

    # The token minted before the reset is now worthless.
    with pytest.raises(AuthenticationError, match="revoked"):
        _await(svc.refresh(live_token))


def test_family_check_fails_open_when_cache_is_unavailable(monkeypatch):
    """An unreachable cache must not lock every user out of their session."""

    class BrokenCache(MemoryCache):
        async def get(self, key: str) -> str | None:
            raise ConnectionError("redis down")

    monkeypatch.setattr(
        "backend.app.services.auth_service.get_cache_service", lambda: BrokenCache()
    )
    _fake, svc, user = _seeded_service()

    # Still able to refresh: the guard degrades, it does not deny.
    assert _await(svc.refresh(create_refresh_token(subject=user.id)))
