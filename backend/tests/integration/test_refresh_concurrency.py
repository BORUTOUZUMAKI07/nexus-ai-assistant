"""Refresh-token rotation under concurrency and replay.

The bug these cover: two tabs whose access tokens expired together both present
the same httpOnly refresh cookie. The server could not tell that apart from a
stolen token being replayed, so the loser of the race was treated as an attacker
and every session for the user was revoked -- including the token the winner had
just been handed. Reproduced before the fix as:

    concurrent exchange statuses: [200, 401]
    follow-up with the rotated token: 401
    "Session has been revoked. Please sign in again."

Reuse detection itself is a security property and is kept: a replay *outside* the
grace window still revokes the family. What changed is that a duplicate inside
the window is a race, not a theft.
"""
import asyncio
import time
import uuid

import pytest
from backend.app.core.config import settings
from backend.app.core.security import decode_token
from backend.app.services.auth_service import _REPLAY_PUBLISH_WAIT_SECONDS


async def _signed_in(client, prefix: str) -> tuple[str, str]:
    """Register + log in, returning (email, refresh_token)."""
    email = f"{prefix}-{uuid.uuid4().hex[:8]}@example.com"
    password = "StrongPass123!"
    registered = await client.post(
        "/api/v1/auth/register",
        json={
            "email": email,
            "username": f"{prefix}-{uuid.uuid4().hex[:6]}",
            "password": password,
        },
    )
    assert registered.status_code == 201, registered.text
    login = await client.post(
        "/api/v1/auth/login",
        data={"username": email, "password": password},
    )
    assert login.status_code == 200, login.text
    return email, login.json()["refresh_token"]


@pytest.mark.asyncio
async def test_concurrent_refresh_of_one_token_keeps_the_session_alive(
    client, redis_backend
):
    """The reported bug: two tabs, one cookie, session must survive.

    Both requests are genuinely concurrent, so this exercises the real race
    rather than a sequential replay. Exactly one may mint new tokens; the other
    must be answered with those same tokens, not a rejection.
    """
    _email, original = await _signed_in(client, "race")

    first, second = await asyncio.gather(
        client.post("/api/v1/auth/refresh", json={"refresh_token": original}),
        client.post("/api/v1/auth/refresh", json={"refresh_token": original}),
    )
    statuses = sorted([first.status_code, second.status_code])
    assert statuses == [200, 200], (
        f"a duplicate exchange inside the grace window must succeed, got {statuses}"
    )

    # Both callers must end up holding the same session. They could not before:
    # one rotation means one new token, so the duplicate has to be answered with
    # that same rotation rather than minting a rival one.
    assert first.json()["refresh_token"] == second.json()["refresh_token"], (
        "the two tabs were handed different sessions; one is already invalidated"
    )

    # Whichever one the caller keeps, the session has to still be usable --
    # this is the assertion that failed before the fix.
    rotated = first.json()["refresh_token"]
    follow_up = await client.post(
        "/api/v1/auth/refresh", json={"refresh_token": rotated}
    )
    assert follow_up.status_code == 200, (
        f"session was destroyed by a benign race: {follow_up.text}"
    )


@pytest.mark.asyncio
async def test_duplicate_refresh_returns_the_same_tokens_not_a_second_rotation(
    client, redis_backend
):
    """The grace path must replay the first result, not mint new tokens.

    If it minted a second rotation, the two callers would hold different tokens
    and one of them would be silently invalidated -- trading a logout for a
    subtler version of the same problem.
    """
    _email, original = await _signed_in(client, "dupe")

    first = await client.post(
        "/api/v1/auth/refresh", json={"refresh_token": original}
    )
    assert first.status_code == 200
    duplicate = await client.post(
        "/api/v1/auth/refresh", json={"refresh_token": original}
    )
    assert duplicate.status_code == 200
    assert duplicate.json()["refresh_token"] == first.json()["refresh_token"]
    assert duplicate.json()["access_token"] == first.json()["access_token"]


@pytest.mark.asyncio
async def test_replay_outside_the_grace_window_still_revokes_the_family(
    client, redis_backend
):
    """Security must not regress: a late replay is theft, and is refused.

    Closes the window explicitly rather than sleeping through it, so the test is
    fast and deterministic.
    """
    _email, original = await _signed_in(client, "late")

    first = await client.post(
        "/api/v1/auth/refresh", json={"refresh_token": original}
    )
    assert first.status_code == 200

    # Age the grace entry out, simulating a replay long after the race.
    await redis_backend.delete(f"refresh:race:{decode_token(original)['jti']}")

    replay = await client.post(
        "/api/v1/auth/refresh", json={"refresh_token": original}
    )
    assert replay.status_code == 401

    # And the whole family is now dead, not just the one token.
    rotated = first.json()["refresh_token"]
    after = await client.post(
        "/api/v1/auth/refresh", json={"refresh_token": rotated}
    )
    assert after.status_code == 401, (
        f"a detected theft must terminate the family, got {after.status_code}"
    )


@pytest.mark.asyncio
async def test_reuse_revokes_one_sign_in_not_every_device(client, redis_backend):
    """Families scope the blast radius of a theft.

    Before families, one stolen token revoked the user's sessions everywhere.
    Now it revokes only the sign-in it came from, so a stolen web tab does not
    log the owner out of their phone.
    """
    victim = f"victim-{uuid.uuid4().hex[:8]}@example.com"
    password = "StrongPass123!"
    registered = await client.post(
        "/api/v1/auth/register",
        json={
            "email": victim,
            "username": f"victim-{uuid.uuid4().hex[:6]}",
            "password": password,
        },
    )
    assert registered.status_code == 201, registered.text

    async def sign_in_on_another_device() -> str:
        login = await client.post(
            "/api/v1/auth/login",
            data={"username": victim, "password": password},
        )
        assert login.status_code == 200, login.text
        return login.json()["refresh_token"]

    stolen_device_token = await sign_in_on_another_device()
    phone_token = await sign_in_on_another_device()

    # Redeem the stolen one, then replay it late: a detected theft.
    first = await client.post(
        "/api/v1/auth/refresh", json={"refresh_token": stolen_device_token}
    )
    assert first.status_code == 200

    await redis_backend.delete(f"refresh:race:{decode_token(stolen_device_token)['jti']}")

    replay = await client.post(
        "/api/v1/auth/refresh", json={"refresh_token": stolen_device_token}
    )
    assert replay.status_code == 401

    # The phone's session is a different family and must be untouched.
    phone_refresh = await client.post(
        "/api/v1/auth/refresh", json={"refresh_token": phone_token}
    )
    assert phone_refresh.status_code == 200, (
        f"a theft on one device logged the user out of another: {phone_refresh.text}"
    )


@pytest.mark.asyncio
async def test_replay_wait_is_bounded_by_a_sub_window(client, redis_backend):
    """A replay must not park a request open for the whole grace window.

    The duplicate polls for the in-flight winner, but that wait is deliberately
    capped at ``_REPLAY_PUBLISH_WAIT_SECONDS`` rather than the full
    ``REFRESH_REUSE_GRACE_SECONDS``. Without the cap, an attacker replaying a
    stolen token could hold a request (and a DB session) for the entire window,
    turning one stolen token into a cheap way to pin server resources -- 20
    refreshes a minute per IP, each one held open.

    The cap is asserted, not assumed: with the window widened to 60s, a replay
    must still be refused promptly. The "0 disables waiting entirely" branch is
    covered separately by
    ``test_grace_window_of_zero_restores_strict_single_use``.
    """
    _email, original = await _signed_in(client, "bounded")
    first = await client.post(
        "/api/v1/auth/refresh", json={"refresh_token": original}
    )
    assert first.status_code == 200

    # Drop the published result so the replay is unambiguously late, then widen
    # the window. Both are needed: with the entry still cached this would be a
    # legitimate in-window duplicate, and with a narrow window the wait would be
    # capped by the window rather than by the service bound.
    await redis_backend.delete(f"refresh:race:{decode_token(original)['jti']}")
    original_window = settings.REFRESH_REUSE_GRACE_SECONDS
    settings.REFRESH_REUSE_GRACE_SECONDS = 60
    try:
        started = time.monotonic()
        replay = await client.post(
            "/api/v1/auth/refresh", json={"refresh_token": original}
        )
        elapsed = time.monotonic() - started
    finally:
        settings.REFRESH_REUSE_GRACE_SECONDS = original_window

    assert replay.status_code == 401
    assert elapsed < 10, (
        f"the duplicate waited {elapsed:.1f}s of a 60s window; the wait is "
        f"supposed to be capped at {_REPLAY_PUBLISH_WAIT_SECONDS}s"
    )


@pytest.mark.asyncio
async def test_grace_window_of_zero_restores_strict_single_use(client, redis_backend):
    """The escape hatch works: 0 means every duplicate is treated as theft."""
    _email, original = await _signed_in(client, "strict")
    original_window = settings.REFRESH_REUSE_GRACE_SECONDS
    settings.REFRESH_REUSE_GRACE_SECONDS = 0
    try:
        first = await client.post(
            "/api/v1/auth/refresh", json={"refresh_token": original}
        )
        assert first.status_code == 200
        duplicate = await client.post(
            "/api/v1/auth/refresh", json={"refresh_token": original}
        )
        assert duplicate.status_code == 401
    finally:
        settings.REFRESH_REUSE_GRACE_SECONDS = original_window
