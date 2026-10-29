"""
Refresh-token rotation, race handling and family revocation.

Presenting the same refresh token twice is ambiguous by nature: either it leaked,
or two parties hold the same credential -- and the app cannot tell those apart
at the moment of presentation. What it *can* do is bound the damage.

Three properties are covered here, all through the ``ICacheService`` seam (a
fake, never a real Redis):

  1. a duplicate *inside* ``REFRESH_REUSE_GRACE_SECONDS`` is a race, not a
     theft, and is answered with the tokens the first exchange already issued.
     This is the two-tabs-expired-together case that used to log the user out of
     every device they owned.
  2. a duplicate *outside* the window is a replay, and refuses the exchange.
  3. that refusal revokes the token's *family* -- every session descended from
     one sign-in -- and nothing wider. A stolen token on one device must not end
     the user's session on another.
"""
import asyncio
from datetime import timedelta
from uuid import uuid4

import pytest
from backend.app.core.exceptions import AuthenticationError
from backend.app.core.security import (
    create_access_token,
    create_refresh_token,
    decode_token,
    get_password_hash,
)
from backend.app.domain.user.models import User
from backend.app.domain.user.schemas import TokenResponse
from backend.app.services.auth_service import AuthService
from backend.tests.fakes import FakeSession


class MemoryCache:
    """In-memory stand-in for the Redis-backed ``ICacheService``.

    Only the primitives AuthService.refresh uses are implemented, which keeps
    the test honest about the interface surface the service depends on.
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

    async def delete(self, key: str) -> None:
        """Not used by the service -- only to age a cache entry out of existence,
        standing in for a grace window that has elapsed in real time."""
        self.store.pop(key, None)


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


def _seeded_service(password: str = "CorrectHorse123!") -> tuple[FakeSession, AuthService, User]:
    fake = FakeSession()
    user = User(
        email=f"refresh-{uuid4().hex[:8]}@nexus.ai",
        username=f"user{uuid4().hex[:6]}",
        hashed_password=get_password_hash(password),
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


def _forget_published_result(cache: MemoryCache, token: str) -> None:
    """Age out the published exchange result, as a real grace window expiring.

    Without this a duplicate is always inside the window and is served the
    winner's tokens, so the replay path is never reached.
    """
    _await(cache.delete(f"refresh:race:{decode_token(token)['jti']}"))


def test_duplicate_inside_the_window_replays_the_first_result(cache):
    """Two tabs expiring together must not log anyone out.

    Both send the same cookie. The second is refused its own rotation and given
    the first one's tokens instead -- so the two tabs hold *identical* tokens
    rather than the second silently invalidating the first.
    """
    _fake, svc, user = _seeded_service()
    token = create_refresh_token(subject=user.id, family_id="fam-race")

    first = _await(svc.refresh(token))
    second = _await(svc.refresh(token))

    assert second.refresh_token == first.refresh_token
    assert second.access_token == first.access_token


def test_late_replay_revokes_the_whole_family(cache):
    """A replay *outside* the window still kills the sessions it descends from.

    The rotated token was never presented twice -- it is the continuation of the
    stolen sign-in, so it has to die with it. Without this the "reuse detection"
    claim would amount to little more than refusing one request.
    """
    _fake, svc, user = _seeded_service()
    token = create_refresh_token(subject=user.id, family_id="fam-stolen")

    rotated = _await(svc.refresh(token)).refresh_token
    _forget_published_result(cache, token)

    with pytest.raises(AuthenticationError, match="already been used"):
        _await(svc.refresh(token))

    with pytest.raises(AuthenticationError, match="revoked"):
        _await(svc.refresh(rotated))


def test_late_replay_leaves_other_sign_ins_alone(cache):
    """The blast radius is one sign-in, not the whole user.

    This is the fix. Revoking per-user meant one replayed token on a laptop
    logged the owner out of the phone they were reading on.
    """
    _fake, svc, user = _seeded_service()
    stolen = create_refresh_token(subject=user.id, family_id="fam-laptop")
    phone = create_refresh_token(subject=user.id, family_id="fam-phone")

    _await(svc.refresh(stolen))
    live_on_phone = _await(svc.refresh(phone)).refresh_token
    _forget_published_result(cache, stolen)

    with pytest.raises(AuthenticationError, match="already been used"):
        _await(svc.refresh(stolen))

    # Still usable, and still in its own family: revoking the laptop's sign-in
    # did not reach across to the phone.
    renewed = _await(svc.refresh(live_on_phone))
    assert decode_token(renewed.refresh_token)["fam"] == "fam-phone"


def test_revocation_marker_is_written_with_a_refresh_lifetime(cache):
    """The marker must expire with the token it invalidates, not outlive it."""
    _fake, svc, user = _seeded_service()
    token = create_refresh_token(subject=user.id, family_id="fam-ttl")
    _await(svc.refresh(token))
    _forget_published_result(cache, token)
    with pytest.raises(AuthenticationError):
        _await(svc.refresh(token))

    # Scoped to the family, so it is the family that has to be marked.
    marker = "refresh:revoked:fam-ttl"
    assert marker in cache.store
    assert (marker, 30 * 86400) in cache.sets


def test_family_revocation_can_be_disabled(cache, monkeypatch):
    """REFRESH_REVOKE_ON_REUSE=False refuses the replay but revokes nothing."""
    monkeypatch.setattr(
        "backend.app.services.auth_service.settings.REFRESH_REVOKE_ON_REUSE", False
    )
    _fake, svc, user = _seeded_service()
    token = create_refresh_token(subject=user.id, family_id="fam-nocascade")

    rotated = _await(svc.refresh(token)).refresh_token
    _forget_published_result(cache, token)
    with pytest.raises(AuthenticationError, match="already been used"):
        _await(svc.refresh(token))

    # The family survives, so the continuation still works.
    assert _await(svc.refresh(rotated)).refresh_token != rotated
    assert "refresh:revoked:fam-nocascade" not in cache.store


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


def test_sign_in_clears_a_standing_user_wide_revocation(cache):
    """A successful sign-in must lift a revocation the account is under.

    The bug this pins: a *user-wide* ``refresh:revoked:{user_id}`` marker is
    written by a password reset (``reset_password``) and by a logout whose family
    cannot be resolved. ``refresh`` honours that marker on every exchange, so
    without a clear it survived the next sign-in and 401'd every rotation for its
    full TTL -- the user logs in fine and is then bounced back to the login page
    on the next refresh, on every device, for REFRESH_TOKEN_EXPIRE_DAYS.
    """
    _fake, svc, user = _seeded_service()

    # A reset writes the user-wide marker and cuts existing sessions.
    reset_token = create_access_token(
        subject=user.id,
        expires_delta=timedelta(minutes=30),
        token_type="reset_password",
    )
    _await(svc.reset_password(reset_token, "BrandNewPass123!"))
    assert f"refresh:revoked:{user.id}" in cache.store

    # The owner signs in again with the new password.
    pair = _await(svc.login(user.email, "BrandNewPass123!"))

    # The marker is gone, so the freshly minted token actually rotates.
    assert f"refresh:revoked:{user.id}" not in cache.store
    rotated = _await(svc.refresh(pair.refresh_token))
    assert rotated.refresh_token != pair.refresh_token


def test_sign_in_after_a_revoked_family_does_not_reuse_the_dead_family(cache):
    """The clear must not resurrect the *old* family's revoke.

    A login only ever lifts the account-wide marker (and any marker for the new
    family it just minted). A revocation scoped to a *different* family -- the
    sign-in whose token was stolen -- must still be honoured, or the theft
    detection this file exists for would be undone by the next login.
    """
    _fake, svc, user = _seeded_service()
    stolen = create_refresh_token(subject=user.id, family_id="fam-compromised")
    _await(svc.refresh(stolen))
    _forget_published_result(cache, stolen)
    with pytest.raises(AuthenticationError, match="already been used"):
        _await(svc.refresh(stolen))  # revokes fam-compromised

    pair = _await(svc.login(user.email, "CorrectHorse123!"))
    new_family = decode_token(pair.refresh_token)["fam"]
    assert new_family != "fam-compromised"

    # The stolen family stays dead; the fresh one works.
    assert "refresh:revoked:fam-compromised" in cache.store
    with pytest.raises(AuthenticationError, match="revoked"):
        _await(svc.refresh(create_refresh_token(subject=user.id, family_id="fam-compromised")))
    assert _await(svc.refresh(pair.refresh_token)).refresh_token != pair.refresh_token


def test_clearing_the_revocation_fails_open_when_cache_is_unavailable(monkeypatch):
    """A cache that cannot delete must not turn a valid sign-in into an error."""

    class BrokenDeleteCache(MemoryCache):
        async def delete(self, key: str) -> None:
            raise ConnectionError("redis down")

    monkeypatch.setattr(
        "backend.app.services.auth_service.get_cache_service",
        lambda: BrokenDeleteCache(),
    )
    _fake, svc, user = _seeded_service()

    # Still signs in: the clear degrades, it does not deny.
    assert _await(svc.login(user.email, "CorrectHorse123!")).access_token
