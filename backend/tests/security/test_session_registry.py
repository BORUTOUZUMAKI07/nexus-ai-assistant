"""Session inventory registry: record, list, refresh and revoke sign-in sessions.

A session is keyed by the refresh-token family id (``fam``), not by the access
token ``jti``: jti rotates on every refresh while fam survives for the whole
sign-in. The registry is best-effort Redis state, so a cache outage must never
break login or sign-out.
"""

import json
from uuid import uuid4

import pytest
from backend.app.services.auth import sessions as registry


class FakeCache:
    """Only the four cache primitives the registry uses."""

    def __init__(self) -> None:
        self.store: dict[str, str] = {}
        self.set_calls: list[tuple[str, int | None]] = []

    async def get(self, key: str) -> str | None:
        return self.store.get(key)

    async def set(self, key: str, value: str, ttl_seconds: int | None = None) -> bool:
        self.store[key] = value
        self.set_calls.append((key, ttl_seconds))
        return True

    async def delete(self, key: str) -> int:
        return self.store.pop(key, None) is not None

    async def scan(self, prefix: str, limit: int = 1000) -> list[str]:
        return [key for key in self.store if key.startswith(prefix)][:limit]


@pytest.fixture
def cache(monkeypatch):
    fake = FakeCache()
    monkeypatch.setattr(registry, "get_cache_service", lambda: fake)
    return fake


@pytest.mark.asyncio
async def test_record_session_stores_metadata_with_a_bounded_ttl(cache):
    user_id, family_id = uuid4(), "fam-a"
    expires_at = 1_900_000_000

    await registry.record_session(user_id, family_id, expires_at, "Mozilla/5.0")

    data = json.loads(cache.store[registry.session_key(user_id, family_id)])
    assert data["user_agent"] == "Mozilla/5.0"
    assert data["expires_at"] == expires_at
    assert data["created_at"] == data["last_seen"]
    ttl = cache.set_calls[-1][1]
    assert ttl is not None and 0 < ttl <= expires_at


@pytest.mark.asyncio
async def test_list_sessions_returns_only_the_callers_sessions_newest_first(cache):
    me, other = uuid4(), uuid4()
    await registry.record_session(me, "old", 100, "Old device")
    await registry.record_session(other, "not-mine", 100, "Other")
    await registry.record_session(me, "new", 200, "New device")
    data = json.loads(cache.store[registry.session_key(me, "old")])
    data["created_at"] = "2000-01-01T00:00:00+00:00"
    cache.store[registry.session_key(me, "old")] = json.dumps(data)

    rows = await registry.list_sessions(me)

    assert [row["family_id"] for row in rows] == ["new", "old"]
    assert all("not-mine" not in row["family_id"] for row in rows)


@pytest.mark.asyncio
async def test_touch_session_updates_last_seen_without_changing_created_at(cache):
    user_id = uuid4()
    await registry.record_session(user_id, "fam-a", 100, "Browser")
    before = json.loads(cache.store[registry.session_key(user_id, "fam-a")])

    await registry.touch_session(user_id, "fam-a", 200)
    after = json.loads(cache.store[registry.session_key(user_id, "fam-a")])

    assert after["created_at"] == before["created_at"]
    assert after["expires_at"] == 200
    assert after["last_seen"] >= before["last_seen"]


@pytest.mark.asyncio
async def test_delete_session_removes_only_the_target(cache):
    user_id = uuid4()
    await registry.record_session(user_id, "fam-a", 100, "A")
    await registry.record_session(user_id, "fam-b", 100, "B")

    await registry.delete_session(user_id, "fam-a")

    remaining = await registry.list_sessions(user_id)
    assert [row["family_id"] for row in remaining] == ["fam-b"]


@pytest.mark.asyncio
async def test_registry_fails_open_when_cache_is_unavailable(monkeypatch):
    class BrokenCache:
        async def get(self, key):
            raise RuntimeError("redis down")

        async def set(self, key, value, ttl_seconds=None):
            raise RuntimeError("redis down")

        async def delete(self, key):
            raise RuntimeError("redis down")

        async def scan(self, prefix, limit=1000):
            raise RuntimeError("redis down")

    monkeypatch.setattr(registry, "get_cache_service", lambda: BrokenCache())
    user_id = uuid4()

    assert await registry.record_session(user_id, "fam-a", 100, "A") is None
    assert await registry.list_sessions(user_id) == []
    assert await registry.delete_session(user_id, "fam-a") is None
