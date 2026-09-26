"""
Unit tests for the response cache (exact-normalized query replay, MD §8.5).
Uses a fake cache service so no Redis connection is required.
"""
import json

import pytest
from backend.app.core.config import settings
from backend.app.infrastructure.cache.response_cache import ResponseCache, cache_key, normalize_query


class FakeCache:
    def __init__(self):
        self.store = {}

    async def get(self, key):
        return self.store.get(key)

    async def set(self, key, value, ttl_seconds=None):
        self.store[key] = value
        return True


@pytest.fixture
def cache(monkeypatch):
    fake = FakeCache()
    orig_enabled = settings.RESPONSE_CACHE_ENABLED
    settings.RESPONSE_CACHE_ENABLED = True
    yield ResponseCache(cache=fake)
    settings.RESPONSE_CACHE_ENABLED = orig_enabled


@pytest.mark.asyncio
async def test_normalize_query_canonical():
    assert normalize_query("  Hello  World!!  ") == "hello world"
    assert normalize_query("Hello   World") == normalize_query("hello world")
    # Trailing punctuation is stripped so "France?" and "france ?" unify.
    assert normalize_query("What is the capital of France?") == normalize_query("what is the capital of france ?")


@pytest.mark.asyncio
async def test_cache_key_is_stable_and_scoped():
    k1 = cache_key("user-1", "model-a", "what is 2+2")
    k2 = cache_key("user-1", "model-a", "What is 2+2  ")  # case + whitespace insensitive
    k3 = cache_key("user-2", "model-a", "what is 2+2")
    assert k1 == k2
    assert k1 != k3
    assert "user-1" in k1


@pytest.mark.asyncio
async def test_get_returns_none_when_disabled(monkeypatch):
    fake = FakeCache()
    orig = settings.RESPONSE_CACHE_ENABLED
    settings.RESPONSE_CACHE_ENABLED = False
    try:
        rc = ResponseCache(cache=fake)
        assert await rc.get("u", "m", "hello world") is None
    finally:
        settings.RESPONSE_CACHE_ENABLED = orig


@pytest.mark.asyncio
async def test_set_get_roundtrip(cache):
    await cache.set("u1", "model-a", "What is the capital of France?", "Paris.", tokens_input=5, tokens_output=1, cost_usd=0.0001)
    payload = await cache.get("u1", "model-a", "what IS the capital of france ?")
    assert payload is not None
    assert payload["content"] == "Paris."
    assert payload["tokens_input"] == 5
    assert payload["cost_usd"] == 0.0001


@pytest.mark.asyncio
async def test_short_queries_not_cached(cache):
    await cache.set("u1", "m", "hi", "Hey there.", tokens_input=1, tokens_output=1, cost_usd=0.0)
    assert await cache.get("u1", "m", "hi") is None


@pytest.mark.asyncio
async def test_cache_misses_for_other_user(cache):
    await cache.set("u1", "model-a", "What is 2+2", "4.", tokens_input=1, tokens_output=1, cost_usd=0.0)
    assert await cache.get("u2", "model-a", "What is 2+2") is None


@pytest.mark.asyncio
async def test_fail_open_on_corrupt_payload(cache):
    cache._cache.store[cache_key("u1", "m", "q q q q")] = "not-json{"
    assert await cache.get("u1", "m", "q q q q") is None