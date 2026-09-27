"""
Unit tests for the resilience guards (HLD: circuit breaker, bulkhead in-flight
cap, singleton-task lock, Idempotency-Key replay), the LiteLLMService
integration seams, and the conversation-delete vector purge.

All tests run against in-memory fakes — no Docker, no Redis, no Qdrant.
"""
import asyncio
from types import SimpleNamespace
from uuid import uuid4

import pytest
from backend.app.domain.conversation.models import Conversation
from backend.app.domain.file.models import File
from backend.app.infrastructure.ai.litellm_client import LiteLLMService
from backend.app.infrastructure.resilience.guards import (
    CircuitBreaker,
    IdempotencyGuard,
    InFlightLimiter,
    TaskLockStore,
    singleton_lock,
)
from backend.app.services import conversation_service as conv_module
from backend.app.services.conversation_service import ConversationService
from fakes import FakeSession

# ── Circuit breaker ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_breaker_allows_while_closed():
    breaker = CircuitBreaker(failure_threshold=3, open_timeout_seconds=60.0)
    assert await breaker.allow("fast_chat") is True
    assert breaker.stats("fast_chat")["state"] == "closed"


@pytest.mark.asyncio
async def test_breaker_opens_after_threshold_and_blocks():
    breaker = CircuitBreaker(failure_threshold=3, open_timeout_seconds=60.0)
    for _ in range(3):
        await breaker.record_failure("groq")
    assert breaker.stats("groq")["state"] == "open"
    assert await breaker.allow("groq") is False


@pytest.mark.asyncio
async def test_breaker_half_open_probe_recovers_on_success():
    breaker = CircuitBreaker(failure_threshold=2, open_timeout_seconds=0.05)
    await breaker.record_failure("fast_chat")
    await breaker.record_failure("fast_chat")
    assert await breaker.allow("fast_chat") is False  # open
    await asyncio.sleep(0.06)  # open timeout elapses → half-open
    assert await breaker.allow("fast_chat") is True  # single probe passes
    assert await breaker.allow("fast_chat") is False  # concurrent caller still blocked
    await breaker.record_success("fast_chat")
    assert breaker.stats("fast_chat")["state"] == "closed"


@pytest.mark.asyncio
async def test_breaker_probe_failure_reopens():
    breaker = CircuitBreaker(failure_threshold=2, open_timeout_seconds=0.05)
    await breaker.record_failure("g")
    await breaker.record_failure("g")
    await asyncio.sleep(0.06)
    assert await breaker.allow("g") is True  # probe granted
    await breaker.record_failure("g")  # probe failed
    assert breaker.stats("g")["state"] == "open"
    assert await breaker.allow("g") is False


# ── In-flight limiter (bulkhead) ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_in_flight_caps_concurrency():
    limiter = InFlightLimiter(max_in_flight=2)
    state = {"active": 0, "peak": 0, "done": 0}

    async def worker():
        async with limiter.acquire("chat"):
            state["active"] += 1
            state["peak"] = max(state["peak"], state["active"])
            await asyncio.sleep(0.02)
            state["active"] -= 1
            state["done"] += 1

    await asyncio.gather(*[worker() for _ in range(6)])
    assert state["peak"] <= 2
    assert state["done"] == 6


# ── Singleton task lock ──────────────────────────────────────────────────────


class FakeCacheStore:
    """In-memory ICacheService subset: set_if_absent + delete, optional failure."""

    def __init__(self):
        self.keys = {}
        self.fail = False

    async def set_if_absent(self, key, value, ttl_seconds):
        if self.fail:
            raise RuntimeError("redis down")
        if key in self.keys:
            return False
        self.keys[key] = value
        return True

    async def delete(self, key):
        self.keys.pop(key, None)
        return 1


@pytest.mark.asyncio
async def test_singleton_lock_mutual_exclusion():
    store = TaskLockStore(cache=FakeCacheStore())
    assert await store.try_acquire("job", 300) is True
    assert await store.try_acquire("job", 300) is False
    await store.release("job")
    assert await store.try_acquire("job", 300) is True


@pytest.mark.asyncio
async def test_singleton_lock_skips_concurrent_second_holder():
    store = TaskLockStore(cache=FakeCacheStore())
    results = []

    async def holder():
        async with singleton_lock("job", ttl_seconds=300, store=store) as acquired:
            results.append(("first", acquired))
            # While we still hold the lock, a second worker must be skipped.
            async with singleton_lock("job", ttl_seconds=300, store=store) as second:
                results.append(("second", second))
            return acquired

    assert await holder() is True
    assert results == [("first", True), ("second", False)]


@pytest.mark.asyncio
async def test_singleton_lock_skips_on_store_failure():
    cache = FakeCacheStore()
    cache.fail = True
    store = TaskLockStore(cache=cache)
    async with singleton_lock("job", ttl_seconds=300, store=store) as acquired:
        assert acquired is False


# ── Idempotency guard ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_idempotency_first_use_allowed_replay_rejected():
    guard = IdempotencyGuard(cache=FakeCacheStore())
    assert await guard.check_and_mark("tool.execute", "user-1", "abc-123") is True
    assert await guard.check_and_mark("tool.execute", "user-1", "abc-123") is False
    # Different key / different user / different scope → always allowed
    assert await guard.check_and_mark("tool.execute", "user-1", "xyz") is True
    assert await guard.check_and_mark("tool.execute", "user-2", "abc-123") is True
    assert await guard.check_and_mark("tool.approval", "user-1", "abc-123") is True


@pytest.mark.asyncio
async def test_idempotency_fail_open_on_store_failure():
    cache = FakeCacheStore()
    cache.fail = True
    guard = IdempotencyGuard(cache=cache)
    assert await guard.check_and_mark("tool.execute", "user-1", "abc") is True


# ── LiteLLMService integration seams ─────────────────────────────────────────


class _FakeResponse:
    def __init__(self, content, model="fake-model"):
        self.content = content
        self.model = model
        self.usage = SimpleNamespace(prompt_tokens=5, completion_tokens=2)
        self.choices = [SimpleNamespace(message=SimpleNamespace(content=content, tool_calls=None))]


class _FakeRouter:
    def __init__(self, fail_groups=()):
        self.calls = []
        self.fail_groups = set(fail_groups)

    async def acompletion(self, model=None, **kwargs):
        self.calls.append(model)
        if model in self.fail_groups:
            raise RuntimeError(f"{model} down")
        return _FakeResponse(content=f"reply from {model}", model=f"resolved/{model}")


@pytest.mark.asyncio
async def test_llm_complete_skips_circuit_open_group_and_falls_back():
    breaker = CircuitBreaker(failure_threshold=2, open_timeout_seconds=60.0)
    router = _FakeRouter()
    await breaker.record_failure("fast_chat")
    await breaker.record_failure("fast_chat")  # primary group OPEN

    svc = LiteLLMService(llm_router=router, circuit_breaker=breaker, in_flight_limiter=InFlightLimiter(8))
    res = await svc.complete([{"role": "user", "content": "hi"}], model="fast_chat")

    assert "fast_chat" not in router.calls
    assert router.calls[0] == "large_context"  # first healthy fallback
    assert res["content"] == "reply from large_context"
    assert breaker.stats("large_context")["state"] == "closed"  # success recorded


@pytest.mark.asyncio
async def test_llm_complete_open_everywhere_raises_without_calls():
    breaker = CircuitBreaker(failure_threshold=1, open_timeout_seconds=60.0)
    router = _FakeRouter()
    for g in ("fast_chat", "large_context", "liquid_fallback", "vision_analysis"):
        await breaker.record_failure(g)

    svc = LiteLLMService(llm_router=router, circuit_breaker=breaker, in_flight_limiter=InFlightLimiter(8))
    with pytest.raises(RuntimeError, match="circuit open"):
        await svc.complete([{"role": "user", "content": "hi"}], model="fast_chat")
    assert router.calls == []


@pytest.mark.asyncio
async def test_llm_complete_records_failure_and_falls_back():
    breaker = CircuitBreaker(failure_threshold=5, open_timeout_seconds=60.0)
    router = _FakeRouter(fail_groups={"fast_chat"})

    svc = LiteLLMService(llm_router=router, circuit_breaker=breaker, in_flight_limiter=InFlightLimiter(8))
    res = await svc.complete([{"role": "user", "content": "hi"}], model="fast_chat")

    assert router.calls == ["fast_chat", "large_context"]
    assert res["content"] == "reply from large_context"
    assert breaker.stats("fast_chat")["failures"] == 1  # counted, not yet open


@pytest.mark.asyncio
async def test_in_flight_limiter_applied_on_complete():
    limiter = InFlightLimiter(max_in_flight=2)
    peek = {"peak": 0, "active": 0}

    class _SlowRouter(_FakeRouter):
        async def acompletion(self, model=None, **kwargs):
            peek["active"] += 1
            peek["peak"] = max(peek["peak"], peek["active"])
            await asyncio.sleep(0.05)
            peek["active"] -= 1
            return await super().acompletion(model=model, **kwargs)

    svc = LiteLLMService(llm_router=_SlowRouter(), circuit_breaker=CircuitBreaker(), in_flight_limiter=limiter)
    await asyncio.gather(*[svc.complete([{"role": "user", "content": "hi"}], model="fast_chat") for _ in range(6)])
    assert peek["peak"] <= 2


# ── Conversation delete → vector purge (saga/reconciliation) ─────────────────


class _FakeConvRepo:
    def __init__(self):
        self.conversations = []
        self.deleted = []

    async def get_by_id(self, conversation_id, user_id=None):
        for c in self.conversations:
            if c.id == conversation_id:
                return c
        return None

    async def delete(self, conv):
        self.deleted.append(conv)


class FakeVectorStore:
    def __init__(self):
        self.purged = []

    async def delete_by_filter(self, filter_conditions):
        self.purged.append(filter_conditions)


def test_conversation_delete_purges_vectors(monkeypatch):
    fake = FakeSession()
    conv = Conversation(user_id=uuid4(), title="t")
    fake.seed(Conversation, [conv])
    file1 = File(
        user_id=conv.user_id,
        conversation_id=conv.id,
        filename="a.pdf",
        original_filename="a.pdf",
        mime_type="application/pdf",
        size_bytes=10,
        status="indexed",
    )
    fake.seed(File, [file1])

    vector = FakeVectorStore()
    repo = _FakeConvRepo()
    repo.conversations = [conv]
    monkeypatch.setattr(conv_module, "ConversationRepository", lambda session: repo)

    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(
            ConversationService(fake, vector_store=vector).delete_conversation(conv.id, conv.user_id)
        )
    finally:
        loop.close()

    assert repo.deleted == [conv]
    assert vector.purged == [{"file_id": str(file1.id)}]


def test_conversation_delete_purge_failure_is_fail_open(monkeypatch):
    fake = FakeSession()
    conv = Conversation(user_id=uuid4(), title="t")
    fake.seed(Conversation, [conv])

    class _ExplodingVector:
        async def delete_by_filter(self, filter_conditions):
            raise RuntimeError("qdrant down")

    repo = _FakeConvRepo()
    repo.conversations = [conv]
    monkeypatch.setattr(conv_module, "ConversationRepository", lambda session: repo)

    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(
            ConversationService(fake, vector_store=_ExplodingVector()).delete_conversation(conv.id, conv.user_id)
        )
    finally:
        loop.close()

    assert repo.deleted == [conv]  # DB deletion still happened
