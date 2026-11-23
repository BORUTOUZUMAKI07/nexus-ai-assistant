"""AuthService must keep the session inventory in step with real revocation.

The inventory is a convenience index, but a stale row is a lie: the settings
page would offer to revoke a session that no longer exists, or hide one that is
still live. These tests pin the four moments that change that state.
"""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from backend.app.core.security import (
    create_refresh_token,
    decode_token,
    get_password_hash,
)
from backend.app.domain.user.models import User
from backend.app.services.auth import sessions as registry
from backend.app.services.auth.service import AuthService
from backend.tests.fakes import FakeSession


class Cache:
    """Duck-typed ICacheService with just the primitives the auth flow touches."""

    def __init__(self) -> None:
        self.store: dict[str, str] = {}
        self.race_response: dict[str, str] = {}

    async def get(self, key: str) -> str | None:
        return self.store.get(key)

    async def set(self, key: str, value: str, ttl_seconds: int | None = None) -> bool:
        self.store[key] = value
        return True

    async def delete(self, key: str) -> int:
        return self.store.pop(key, None) is not None

    async def scan(self, prefix: str, limit: int = 1000) -> list[str]:
        return [key for key in self.store if key.startswith(prefix)][:limit]

    async def rotate_refresh_token(
        self, jti, ttl_seconds, grace_window_seconds, race_response=None
    ) -> dict[str, str | None]:
        if jti in self.store:
            cached = self.race_response.get(jti)
            return {"action": "race_replay", "data": cached} if cached else {"action": "theft"}
        self.store[jti] = "1"
        if race_response:
            self.race_response[jti] = race_response
        return {"action": "first_use"}


@pytest.fixture
def cache(monkeypatch):
    fake = Cache()
    monkeypatch.setattr("backend.app.services.auth.service.get_cache_service", lambda: fake)
    monkeypatch.setattr(registry, "get_cache_service", lambda: fake)
    return fake


def _service() -> tuple[AuthService, User]:
    store = FakeSession()
    user = User(
        email=f"session-{uuid4().hex[:8]}@nexus.ai",
        username=f"user{uuid4().hex[:6]}",
        hashed_password=get_password_hash("CorrectHorse123!"),
        is_active=True,
        is_verified=True,
    )
    store.seed(User, [user])
    return AuthService(store), user


@pytest.mark.asyncio
async def test_login_records_a_session_with_the_browser_label(cache):
    svc, user = _service()

    await svc.login(user.email, "CorrectHorse123!", user_agent="Mozilla/5.0 (Windows)")

    rows = await registry.list_sessions(user.id)
    assert len(rows) == 1
    assert rows[0]["user_agent"] == "Mozilla/5.0 (Windows)"


@pytest.mark.asyncio
async def test_refresh_keeps_the_session_visible(cache):
    svc, user = _service()
    tokens = await svc.login(user.email, "CorrectHorse123!", user_agent="Phone")
    family_id = decode_token(tokens.access_token)["fam"]
    before = (await registry.list_sessions(user.id))[0]

    await svc.refresh(create_refresh_token(subject=user.id, family_id=family_id))
    after = (await registry.list_sessions(user.id))[0]

    assert after["family_id"] == family_id
    assert after["created_at"] == before["created_at"]
    assert after["last_seen"] >= before["last_seen"]


@pytest.mark.asyncio
async def test_revoke_session_removes_one_sign_in_and_its_revocation_marker(cache):
    svc, user = _service()
    await svc.login(user.email, "CorrectHorse123!", user_agent="Laptop")
    await svc.login(user.email, "CorrectHorse123!", user_agent="Phone")

    rows = await registry.list_sessions(user.id)
    phone = next(r for r in rows if r["user_agent"] == "Phone")

    assert await svc.revoke_session(user.id, phone["family_id"]) is True

    remaining = await registry.list_sessions(user.id)
    assert [r["user_agent"] for r in remaining] == ["Laptop"]
    assert f"refresh:revoked:{phone['family_id']}" in cache.store


@pytest.mark.asyncio
async def test_revoke_session_refuses_a_family_owned_by_someone_else(cache):
    svc, user = _service()
    other = uuid4()
    await registry.record_session(
        other, "other-family", int((datetime.now(UTC) + timedelta(days=1)).timestamp()), "Other"
    )

    assert await svc.revoke_session(user.id, "other-family") is False

    assert "refresh:revoked:other-family" not in cache.store
    assert len(await registry.list_sessions(other)) == 1
