"""The alternate /messages endpoints must apply the same output guardrails.

``send_message_sync`` persisted and returned the raw model text;
``send_message_stream`` emitted raw tokens, persisted the raw joined response,
and put ``str(exc)`` in an SSE error frame. Neither path applied the PII
redaction the main synthesizer applies. These tests pin the transport boundary:
sanitised text on the wire, in storage, in the cache and in webhooks, with
generic error messages that cannot leak provider credentials or DB details.
"""
from __future__ import annotations

import json
from types import SimpleNamespace
from uuid import uuid4

import pytest
from backend.app.api.v1 import messages
from backend.app.domain.conversation.schemas import MessageCreate
from fastapi import HTTPException

RAW_ANSWER = (
    "Email alice@example.com or call 415-555-2671. "
    "Your SSN 123-45-6789 is on file."
)
CLEAN_ANSWER = (
    "Email [EMAIL_REDACTED] or call [PHONE_US_REDACTED]. "
    "Your SSN [SSN_REDACTED] is on file."
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
    captured = {"cache_set": [], "webhooks": [], "usage": []}
    conv_svc = _conv_service()

    class _Repo:
        session = object()

        async def log_usage(self, payload):
            captured["usage"].append(payload)

    usage_svc = SimpleNamespace(_repo=_Repo())

    class _WebhookService:
        def __init__(self, _session):
            pass

        async def dispatch_event(self, *, event, payload, user_id):
            captured["webhooks"].append((event, payload))

    async def _completion(**_kwargs):
        return RAW_ANSWER

    async def _stream(**_kwargs):
        # Deliberately split every PII value across token boundaries.
        for chunk in (
            "Email alice@",
            "example.com or call 415-",
            "555-2671. Your SSN 123-45",
            "-6789 is on file.",
        ):
            yield chunk

    monkeypatch.setattr(messages, "resolve_answer_model", lambda model: model or "gpt-4o-mini")
    monkeypatch.setattr(messages, "get_last_resolved_model", lambda: None)
    monkeypatch.setattr(messages, "group_primary_model", lambda model: model)
    monkeypatch.setattr(messages, "resolve_provider", lambda _model: "openai")
    monkeypatch.setattr(messages, "cached_tokens_seen", lambda: 0)
    monkeypatch.setattr(messages.experiment_service, "variant_for_user", lambda *_: "default")
    monkeypatch.setattr(
        messages.prompt_compiler, "compile_system_prompt_cached", lambda **_: _value("SYSTEM")
    )
    monkeypatch.setattr(messages.context_compiler, "compile_context", lambda **_: _value([]))
    monkeypatch.setattr(messages.response_cache, "get", lambda *_a, **_k: _value(None))

    async def _cache_set(*args, **kwargs):
        captured["cache_set"].append((args, kwargs))

    monkeypatch.setattr(messages.response_cache, "set", _cache_set)

    import backend.app.services.confidence.bandit as bandit_mod
    import backend.app.services.memory.semantic_cache as semantic_mod
    import backend.app.services.webhooks.service as webhook_mod

    async def _no_cache(*_args, **_kwargs):
        return None

    async def _noop(*_args, **_kwargs):
        return None

    monkeypatch.setattr(bandit_mod.bandit_service, "select", _no_cache)
    monkeypatch.setattr(semantic_mod.semantic_response_cache, "get", _no_cache)
    monkeypatch.setattr(semantic_mod.semantic_response_cache, "set", _noop)
    monkeypatch.setattr(webhook_mod, "WebhookService", _WebhookService)
    monkeypatch.setattr(messages.cost_tracking_service, "record_cost_log", _noop)
    monkeypatch.setattr(messages.ai_client, "completion", _completion)
    monkeypatch.setattr(messages.ai_client, "stream_completion", _stream)
    monkeypatch.setattr(messages.ai_client, "count_tokens", lambda text, _model: len(text.split()))

    return SimpleNamespace(
        conv_svc=conv_svc,
        usage_svc=usage_svc,
        captured=captured,
        user=SimpleNamespace(id=uuid4()),
        conversation_id=uuid4(),
    )


async def _call_sync(harness):
    return await messages.send_message_sync(
        conversation_id=harness.conversation_id,
        message_in=MessageCreate(content="What is on file?"),
        current_user=harness.user,
        org_id=None,
        conv_svc=harness.conv_svc,
        usage_svc=harness.usage_svc,
    )


async def _events(response):
    async for line in response.body_iterator:
        if line.startswith("data: "):
            yield json.loads(line[len("data: "):])


async def _call_stream(harness):
    return await messages.send_message_stream(
        conversation_id=harness.conversation_id,
        message_in=MessageCreate(content="What is on file?"),
        current_user=harness.user,
        org_id=None,
        conv_svc=harness.conv_svc,
        usage_svc=harness.usage_svc,
    )


async def test_sync_response_and_every_sink_are_pii_safe(harness):
    result = await _call_sync(harness)

    assert result.content == CLEAN_ANSWER
    assert harness.conv_svc.saved[-1]["content"] == CLEAN_ANSWER
    assert harness.captured["cache_set"][0][0][3] == CLEAN_ANSWER
    assert harness.captured["webhooks"][0][1]["content"] == CLEAN_ANSWER


async def test_stream_frames_and_persisted_answer_are_pii_safe(harness):
    response = await _call_stream(harness)

    assert response.headers["Connection"] == "keep-alive"
    frames = [event async for event in _events(response)]
    tokens = [frame["token"] for frame in frames if frame["event"] == "token"]

    assert frames[0]["event"] == "start"
    assert frames[-1]["event"] == "done"
    assert len(tokens) > 1, "the endpoint must still stream token by token"
    assert "".join(tokens) == CLEAN_ANSWER
    assert all(
        secret not in token
        for token in tokens
        for secret in ("alice@example.com", "415-555-2671", "123-45-6789")
    )
    assert harness.conv_svc.saved[-1]["content"] == CLEAN_ANSWER


async def test_stream_error_frame_does_not_echo_the_exception(harness, monkeypatch):
    async def _failing_stream(**_kwargs):
        raise RuntimeError(
            "litellm provider rejected Authorization: Bearer sk-secret12345"
        )
        yield "unreachable"  # pragma: no cover

    monkeypatch.setattr(messages.ai_client, "stream_completion", _failing_stream)
    response = await _call_stream(harness)

    frames = [event async for event in _events(response)]
    assert frames[-1]["event"] == "error"
    assert "sk-secret12345" not in json.dumps(frames)
    assert frames[-1]["error"] == "An error occurred while generating the response."


async def test_regenerate_does_not_echo_internal_validation_errors(harness):
    async def _boom(*_args, **_kwargs):
        raise ValueError("database DSN postgresql://user:secret@host/db")

    harness.conv_svc.prepare_regeneration = _boom

    with pytest.raises(HTTPException) as caught:
        await messages.regenerate_message(
            conversation_id=harness.conversation_id,
            message_id=uuid4(),
            current_user=harness.user,
            conv_svc=harness.conv_svc,
        )

    assert caught.value.status_code == 400
    assert "postgresql://" not in str(caught.value.detail)
