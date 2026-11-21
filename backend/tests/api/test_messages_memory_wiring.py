"""Direct /messages endpoints must recall Mem0 memories into their prompt.

The main streaming chat path (orchestrator) loads long-term memories before it
answers, but ``POST /conversations/{id}/messages`` and its SSE variant compiled
their prompt without them. A user who stated a preference in one conversation
was ignored by these alternate endpoints. These tests pin the recall boundary,
including fail-open behaviour when the memory service is unavailable.
"""
from __future__ import annotations

import json
from types import SimpleNamespace
from uuid import uuid4

import backend.app.services.confidence.bandit as bandit_module
import backend.app.services.memory.semantic_cache as semantic_module
import backend.app.services.webhooks.service as webhook_module
import pytest
from backend.app.api.v1 import messages
from backend.app.domain.conversation.schemas import MessageCreate

MEMORY_BLOCK = (
    "### Relevant User Context (from previous conversations):\n"
    "- User prefers dark mode in all tools\n"
)


async def _value(result):
    return result


def _conv_service():
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

    return _ConvService()


@pytest.fixture
def harness(monkeypatch):
    """Minimal direct-call harness for the two /messages endpoints."""
    captured = {"system_prompt": None, "cache_get_variants": [], "cache_set_variants": []}
    conv_svc = _conv_service()

    class _Repo:
        session = object()

        async def log_usage(self, _payload):
            return None

    usage_svc = SimpleNamespace(_repo=_Repo())

    class _WebhookService:
        def __init__(self, _session):
            pass

        async def dispatch_event(self, **_kwargs):
            return None

    async def _completion(**_kwargs):
        return "Here is your answer."

    async def _stream(**_kwargs):
        for chunk in ("Here ", "is ", "your ", "answer."):
            yield chunk

    async def _noop(*_args, **_kwargs):
        return None

    async def _context(**kwargs):
        captured["system_prompt"] = kwargs["system_prompt"]
        return []

    async def _cache_get(*_args, **kwargs):
        captured["cache_get_variants"].append(kwargs.get("prompt_variant"))
        return None

    async def _cache_set(*_args, **kwargs):
        captured["cache_set_variants"].append(kwargs.get("prompt_variant"))

    monkeypatch.setattr(messages, "resolve_answer_model", lambda model: model or "model-a")
    monkeypatch.setattr(messages, "get_last_resolved_model", lambda: None)
    monkeypatch.setattr(messages, "group_primary_model", lambda model: model)
    monkeypatch.setattr(messages, "resolve_provider", lambda _model: "openai")
    monkeypatch.setattr(messages, "cached_tokens_seen", lambda: 0)
    monkeypatch.setattr(messages.experiment_service, "variant_for_user", lambda *_: "default")
    monkeypatch.setattr(
        messages.prompt_compiler, "compile_system_prompt_cached", lambda **_: _value("SYSTEM")
    )
    monkeypatch.setattr(messages.context_compiler, "compile_context", _context)
    monkeypatch.setattr(messages.response_cache, "get", _cache_get)
    monkeypatch.setattr(messages.response_cache, "set", _cache_set)
    monkeypatch.setattr(messages.ai_client, "completion", _completion)
    monkeypatch.setattr(messages.ai_client, "stream_completion", _stream)
    monkeypatch.setattr(messages.ai_client, "count_tokens", lambda text, _model: len(text.split()))
    monkeypatch.setattr(messages.cost_tracking_service, "record_cost_log", _noop)
    monkeypatch.setattr(bandit_module.bandit_service, "select", _noop)
    monkeypatch.setattr(semantic_module.semantic_response_cache, "get", _noop)
    monkeypatch.setattr(semantic_module.semantic_response_cache, "set", _noop)
    monkeypatch.setattr(webhook_module, "WebhookService", _WebhookService)

    return SimpleNamespace(
        conv_svc=conv_svc,
        usage_svc=usage_svc,
        captured=captured,
        user=SimpleNamespace(id=uuid4()),
        conversation_id=uuid4(),
    )


async def _call_sync(harness, content="What should I use?"):
    return await messages.send_message_sync(
        conversation_id=harness.conversation_id,
        message_in=MessageCreate(content=content),
        current_user=harness.user,
        org_id=None,
        conv_svc=harness.conv_svc,
        usage_svc=harness.usage_svc,
    )


async def _stream_events(response):
    async for line in response.body_iterator:
        if line.startswith("data: "):
            yield json.loads(line[len("data: "):])


@pytest.mark.asyncio
async def test_sync_endpoint_injects_mem0_memory_into_the_prompt(monkeypatch, harness):
    """RED: a stated preference must reach the sync endpoint's system prompt."""

    async def _recall(**_kwargs):
        return MEMORY_BLOCK, []

    monkeypatch.setattr(messages.long_term_memory, "build_memory_context_block_with_ids", _recall)

    result = await _call_sync(harness)

    assert result.content == "Here is your answer."
    assert MEMORY_BLOCK in harness.captured["system_prompt"], (
        "the direct sync endpoint must inject the Mem0 block into the system prompt"
    )


@pytest.mark.asyncio
async def test_stream_endpoint_injects_mem0_memory_into_the_prompt(monkeypatch, harness):
    """RED: the SSE variant must recall memories too."""

    async def _recall(**_kwargs):
        return MEMORY_BLOCK, []

    monkeypatch.setattr(messages.long_term_memory, "build_memory_context_block_with_ids", _recall)

    response = await messages.send_message_stream(
        conversation_id=harness.conversation_id,
        message_in=MessageCreate(content="What should I use?"),
        current_user=harness.user,
        org_id=None,
        conv_svc=harness.conv_svc,
        usage_svc=harness.usage_svc,
    )
    frames = [frame async for frame in _stream_events(response)]

    assert frames[0]["event"] == "start"
    assert frames[-1]["event"] == "done"
    assert MEMORY_BLOCK in harness.captured["system_prompt"], (
        "the direct stream endpoint must inject the Mem0 block into the system prompt"
    )


@pytest.mark.asyncio
async def test_memory_outage_does_not_fail_the_sync_reply(monkeypatch, harness):
    """Fail-open: a Mem0 error must never break the user's answer."""

    async def _recall(**_kwargs):
        raise RuntimeError("mem0 is down")

    monkeypatch.setattr(messages.long_term_memory, "build_memory_context_block_with_ids", _recall)

    result = await _call_sync(harness)

    assert result.content == "Here is your answer."
    assert harness.captured["system_prompt"] == "SYSTEM"


@pytest.mark.asyncio
async def test_cache_key_scopes_the_answer_to_the_recalled_memory(monkeypatch, harness):
    """RED: identical questions with different memories must not share a cached answer."""

    async def _recall(**_kwargs):
        return MEMORY_BLOCK, []

    monkeypatch.setattr(messages.long_term_memory, "build_memory_context_block_with_ids", _recall)

    await _call_sync(harness)
    first_variant = harness.captured["cache_get_variants"][-1]

    harness.captured["cache_get_variants"].clear()

    async def _other_recall(**_kwargs):
        return "### Relevant User Context\n- User prefers light mode\n", []

    monkeypatch.setattr(messages.long_term_memory, "build_memory_context_block_with_ids", _other_recall)
    await _call_sync(harness)
    second_variant = harness.captured["cache_get_variants"][-1]

    assert first_variant != second_variant, (
        "a change in recalled memories must produce a different response-cache key"
    )


PREFERENCE = "I prefer dark mode in all my tools"


@pytest.mark.asyncio
async def test_sync_endpoint_writes_a_stated_preference_to_mem0(monkeypatch, harness):
    """RED: direct /messages must save new facts, not only read old ones."""
    writes = []

    async def _add(**kwargs):
        writes.append(kwargs)
        return [{"id": "m1", "memory": PREFERENCE}]

    async def _mirror(self, **kwargs):
        return [], 0

    monkeypatch.setattr(messages.long_term_memory, "add_from_conversation", _add)
    monkeypatch.setattr(messages.UserRepository, "mirror_memories", _mirror)

    await _call_sync(harness, PREFERENCE)

    assert len(writes) == 1
    assert writes[0]["user_id"] == harness.user.id
    assert writes[0]["messages"][0]["content"] == PREFERENCE
    assert str(harness.conversation_id) in str(writes[0]["metadata"])


@pytest.mark.asyncio
async def test_sync_endpoint_mirrors_the_extracted_fact_locally(monkeypatch, harness):
    """RED: a mem0-only fact stays invisible to the user's memory API."""
    mirrors = []

    async def _add(**_kwargs):
        return [{"id": "m1", "memory": PREFERENCE}]

    async def _mirror(self, **kwargs):
        mirrors.append(kwargs)
        return [], 0

    monkeypatch.setattr(messages.long_term_memory, "add_from_conversation", _add)
    monkeypatch.setattr(messages.UserRepository, "mirror_memories", _mirror)

    await _call_sync(harness, PREFERENCE)

    assert mirrors[0]["facts"] == [{"id": "m1", "memory": PREFERENCE}]
    assert mirrors[0]["source_conv_id"] == harness.conversation_id


@pytest.mark.asyncio
async def test_chatter_is_not_written_to_mem0(monkeypatch, harness):
    """The write gate must be shared with the orchestrator chat path."""
    writes = []

    async def _add(**kwargs):
        writes.append(kwargs)
        return []

    monkeypatch.setattr(messages.long_term_memory, "add_from_conversation", _add)

    await _call_sync(harness, "thanks!")

    assert writes == []


@pytest.mark.asyncio
async def test_memory_write_failure_does_not_fail_the_reply(monkeypatch, harness):
    """Fail-open: an embedding or vector outage must not fail the answer."""

    async def _add(**_kwargs):
        raise RuntimeError("mem0 write down")

    monkeypatch.setattr(messages.long_term_memory, "add_from_conversation", _add)

    result = await _call_sync(harness, PREFERENCE)

    assert result.content == "Here is your answer."


@pytest.mark.asyncio
async def test_stream_endpoint_writes_a_stated_preference(monkeypatch, harness):
    """RED: the SSE variant must persist memories like the sync variant."""
    writes = []

    async def _add(**kwargs):
        writes.append(kwargs)
        return [{"id": "m1", "memory": PREFERENCE}]

    async def _mirror(self, **kwargs):
        return [], 0

    monkeypatch.setattr(messages.long_term_memory, "add_from_conversation", _add)
    monkeypatch.setattr(messages.UserRepository, "mirror_memories", _mirror)

    response = await messages.send_message_stream(
        conversation_id=harness.conversation_id,
        message_in=MessageCreate(content=PREFERENCE),
        current_user=harness.user,
        org_id=None,
        conv_svc=harness.conv_svc,
        usage_svc=harness.usage_svc,
    )
    frames = [frame async for frame in _stream_events(response)]

    assert frames[-1]["event"] == "done"
    assert len(writes) == 1


RECALLED_ITEMS = [{"id": "mem0-1", "memory": "User prefers dark mode in all tools"}]


@pytest.mark.asyncio
async def test_sync_endpoint_reinforces_recalled_memories(monkeypatch, harness):
    """RED: a recalled fact must be reinforced after a direct sync answer."""
    reinforced = []

    async def _recall(**_kwargs):
        return MEMORY_BLOCK, RECALLED_ITEMS

    async def _reinforce(user_id, recalled_items):
        reinforced.append((user_id, recalled_items))

    monkeypatch.setattr(messages.long_term_memory, "build_memory_context_block_with_ids", _recall)
    monkeypatch.setattr(messages, "reinforce_recalled_memories", _reinforce)

    await _call_sync(harness)

    assert reinforced == [(str(harness.user.id), RECALLED_ITEMS)]


@pytest.mark.asyncio
async def test_stream_endpoint_reinforces_recalled_memories(monkeypatch, harness):
    """RED: a recalled fact must be reinforced after a direct stream answer."""
    reinforced = []

    async def _recall(**_kwargs):
        return MEMORY_BLOCK, RECALLED_ITEMS

    async def _reinforce(user_id, recalled_items):
        reinforced.append((user_id, recalled_items))

    monkeypatch.setattr(messages.long_term_memory, "build_memory_context_block_with_ids", _recall)
    monkeypatch.setattr(messages, "reinforce_recalled_memories", _reinforce)

    response = await messages.send_message_stream(
        conversation_id=harness.conversation_id,
        message_in=MessageCreate(content="What should I use?"),
        current_user=harness.user,
        org_id=None,
        conv_svc=harness.conv_svc,
        usage_svc=harness.usage_svc,
    )
    frames = [frame async for frame in _stream_events(response)]

    assert frames[-1]["event"] == "done"
    assert reinforced == [(str(harness.user.id), RECALLED_ITEMS)]
