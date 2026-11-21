"""A cached answer must never cross from one conversation into another."""
from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest
from backend.app.api.v1 import messages
from backend.app.core.config import settings
from backend.app.domain.conversation.schemas import MessageCreate
from backend.app.infrastructure.cache.response_cache import ResponseCache
from backend.app.services.confidence import bandit as bandit_mod
from backend.app.services.memory import semantic_cache as semantic_module


class FakeCache:
    def __init__(self):
        self.store = {}

    async def get(self, key):
        return self.store.get(key)

    async def set(self, key, value, ttl_seconds=None):
        self.store[key] = value
        return True

    async def delete(self, key):
        return self.store.pop(key, None) is not None


class _ConvService:
    def __init__(self):
        self.saved = []

    async def get_conversation(self, _conversation_id, *, user_id):
        return SimpleNamespace(system_prompt="You are Nexus AI.")

    async def get_messages(self, _conversation_id):
        return []

    async def add_message(self, **kwargs):
        self.saved.append(kwargs)
        return SimpleNamespace(id=uuid4(), **kwargs)


class _Repo:
    session = object()

    def __init__(self):
        self.rows = []

    async def log_usage(self, payload):
        self.rows.append(payload)


@pytest.fixture
def cache_harness(monkeypatch):
    """Two identical questions, two conversations, one real exact cache."""
    orig_enabled = settings.RESPONSE_CACHE_ENABLED
    settings.RESPONSE_CACHE_ENABLED = True
    fake = FakeCache()
    real_cache = ResponseCache(cache=fake)
    completions = []
    answers = iter(["answer from conversation A", "answer from conversation B"])

    async def _value(result):
        return result

    async def _completion(**kwargs):
        completions.append(kwargs)
        return next(answers)

    async def _noop(*_args, **_kwargs):
        return None

    class _WebhookService:
        def __init__(self, _session):
            pass

        async def dispatch_event(self, **_kwargs):
            return None

    monkeypatch.setattr(messages, "response_cache", real_cache)
    monkeypatch.setattr(messages, "resolve_answer_model", lambda model: model or "model-a")
    monkeypatch.setattr(messages, "get_last_resolved_model", lambda: None)
    monkeypatch.setattr(messages, "group_primary_model", lambda model: model)
    monkeypatch.setattr(messages, "resolve_provider", lambda _model: "openai")
    monkeypatch.setattr(messages, "cached_tokens_seen", lambda: 0)
    monkeypatch.setattr(messages.experiment_service, "variant_for_user", lambda *_: "default")
    monkeypatch.setattr(
        messages.prompt_compiler, "compile_system_prompt_cached", lambda **_: _value("SYSTEM")
    )
    monkeypatch.setattr(messages.context_compiler, "compile_context", lambda **_: _value([]))
    monkeypatch.setattr(
        semantic_module, "semantic_response_cache", SimpleNamespace(get=_noop, set=_noop)
    )
    monkeypatch.setattr(messages.cost_tracking_service, "record_cost_log", _noop)
    monkeypatch.setattr(messages.ai_client, "completion", _completion)
    monkeypatch.setattr(messages.ai_client, "count_tokens", lambda text, _model: len(text.split()))
    monkeypatch.setattr(
        "backend.app.services.webhooks.service.WebhookService", _WebhookService
    )
    monkeypatch.setattr(bandit_mod.bandit_service, "select", _noop)

    yield SimpleNamespace(
        conv_svc=_ConvService(),
        usage_svc=SimpleNamespace(_repo=_Repo()),
        completions=completions,
        user=SimpleNamespace(id=uuid4()),
    )
    settings.RESPONSE_CACHE_ENABLED = orig_enabled


@pytest.mark.asyncio
async def test_identical_question_in_a_different_conversation_is_a_cache_miss(cache_harness):
    async def ask(conversation_id):
        result = await messages.send_message_sync(
            conversation_id=conversation_id,
            message_in=MessageCreate(content="What is on file?"),
            current_user=cache_harness.user,
            org_id=None,
            conv_svc=cache_harness.conv_svc,
            usage_svc=cache_harness.usage_svc,
        )
        return result.content

    first = await ask(uuid4())
    second = await ask(uuid4())

    assert first == "answer from conversation A"
    assert second == "answer from conversation B", (
        "the exact cache must be scoped to the conversation that produced the answer"
    )
    assert len(cache_harness.completions) == 2


@pytest.mark.asyncio
async def test_semantic_cache_does_not_share_paraphrases_across_conversations(monkeypatch):
    """A paraphrase in another thread must not replay this thread's answer."""
    monkeypatch.setattr(settings, "RESPONSE_CACHE_ENABLED", True)
    monkeypatch.setattr(settings, "RESPONSE_CACHE_SEMANTIC_ENABLED", True)

    async def _embed(_text):
        return [1.0, 0.0]

    sem = semantic_module.SemanticResponseCache(
        cache=FakeCache(), embedder=SimpleNamespace(generate_embedding=_embed)
    )
    await sem.set(
        "user-1",
        "model-a",
        "What is on file?",
        "answer from conversation A",
        1,
        1,
        0.0,
        conversation_id="conv-a",
    )

    other_thread = await sem.get(
        "user-1", "model-a", "show me the file", conversation_id="conv-b"
    )
    same_thread = await sem.get(
        "user-1", "model-a", "show me the file", conversation_id="conv-a"
    )

    assert other_thread is None
    assert same_thread["content"] == "answer from conversation A"


@pytest.mark.asyncio
async def test_exact_cache_invalidation_only_drops_the_requested_scope(monkeypatch):
    """A new answer must be able to evict one exact key without touching neighbours."""
    monkeypatch.setattr(settings, "RESPONSE_CACHE_ENABLED", True)
    cache = ResponseCache(cache=FakeCache())
    query = "What is on file?"
    await cache.set("user-1", "model-a", query, "old A1", 1, 1, 0.0, "v1", "conv-a")
    await cache.set("user-1", "model-a", query, "old A2", 1, 1, 0.0, "v2", "conv-a")
    await cache.set("user-1", "model-a", query, "old B1", 1, 1, 0.0, "v1", "conv-b")

    await cache.invalidate("user-1", "model-a", query, "v1", "conv-a")

    assert await cache.get("user-1", "model-a", query, "v1", "conv-a") is None
    assert (await cache.get("user-1", "model-a", query, "v2", "conv-a"))["content"] == "old A2"
    assert (await cache.get("user-1", "model-a", query, "v1", "conv-b"))["content"] == "old B1"
