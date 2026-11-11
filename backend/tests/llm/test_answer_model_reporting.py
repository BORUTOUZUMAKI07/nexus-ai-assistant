"""Tests for user-selectable answer models and the model we report back.

Two things have to be true for the picker to be *real* rather than cosmetic:

  * a user-supplied name must be whitelisted down to one of the two answer
    groups before it reaches the router (an unknown string handed to the router
    as a ``model_group`` drops the request out of the fallback chain entirely),
    and
  * the transcript must report the model that *actually answered*, so a provider
    fallback is visible instead of hidden behind the label the user picked.

The second is a ``ContextVar`` written inside ``complete()`` -- the only place
that sees the resolved provider slug -- and read by the synthesizer immediately
after its draft call. The tests below pin the whitelist, the contextvar's
per-task isolation, and that ``complete()`` records the resolved slug (not the
requested group).

No network and no database: the router is a local double.
"""

from __future__ import annotations

import asyncio
import contextlib
from typing import Any

import pytest
from backend.app.infrastructure.ai import litellm_client as lc

# ── resolve_answer_model: the whitelist ───────────────────────────────────────


def test_no_selection_lands_on_the_documented_default():
    assert lc.resolve_answer_model(None) == lc.DEFAULT_ANSWER_GROUP
    assert lc.resolve_answer_model("") == lc.DEFAULT_ANSWER_GROUP
    assert lc.DEFAULT_ANSWER_GROUP in lc.ANSWER_MODEL_GROUPS


def test_the_two_answer_groups_are_accepted_verbatim():
    for group in sorted(lc.ANSWER_MODEL_GROUPS):
        assert lc.resolve_answer_model(group) == group


def test_a_provider_slug_is_resolved_through_the_alias_table():
    """The picker and the chat route may send either a group or a slug."""
    assert lc.resolve_answer_model("qwen3.8-27b") == "fast_chat"
    assert lc.resolve_answer_model("groq/qwen/qwen3.8-27b") == "fast_chat"
    assert lc.resolve_answer_model("gpt-oss-120b") == "complex_reasoning"
    assert lc.resolve_answer_model("groq/openai/gpt-oss-120b") == "complex_reasoning"


def test_the_legacy_default_alias_still_resolves_to_the_default_group():
    """A stale ``default_model`` in someone's settings must not 500 a turn."""
    assert lc.resolve_answer_model("llama-3.3-70b-versatile") == lc.DEFAULT_ANSWER_GROUP


@pytest.mark.parametrize(
    "unknown",
    [
        "vision_analysis",  # a real group, but not user-selectable
        "vision_fallback",
        "liquid_fallback",
        "gemini_free",  # a real free fallback group, but not user-selectable
        "large_context",
        "gpt-9-turbo-ultra",
        "../../etc/passwd",
        "fast_chat; DROP TABLE users",
    ],
)
def test_anything_outside_the_two_answer_groups_degrades_to_the_default(unknown):
    """A group the user may not pick, or garbage, must not reach the router."""
    assert lc.resolve_answer_model(unknown) == lc.DEFAULT_ANSWER_GROUP


def test_group_primary_model_names_the_configured_deployment():
    assert lc.group_primary_model("fast_chat") == "groq/qwen/qwen3.8-27b"
    assert lc.group_primary_model("complex_reasoning") == "groq/openai/gpt-oss-120b"


def test_group_primary_model_falls_back_to_the_group_name_for_an_unknown_group():
    """Used for the label when nothing better is known; must not raise."""
    assert lc.group_primary_model("no_such_group") == "no_such_group"


# ── get_last_resolved_model: the contextvar ───────────────────────────────────


async def test_the_resolved_model_reflects_the_last_set_value():
    token = lc._last_resolved_model.set("groq/openai/gpt-oss-120b")
    try:
        assert lc.get_last_resolved_model() == "groq/openai/gpt-oss-120b"
    finally:
        lc._last_resolved_model.reset(token)


async def test_a_child_task_cannot_overwrite_the_parents_resolved_model():
    """Per-task, so two concurrent runs cannot read each other's answer.

    A plain instance attribute would let a fast fallback run clobber the slug a
    slower concurrent run is about to report, and the transcript would name the
    wrong model.
    """
    token = lc._last_resolved_model.set("parent-model")
    try:
        async def _child() -> str | None:
            lc._last_resolved_model.set("child-model")
            return lc.get_last_resolved_model()

        assert await asyncio.create_task(_child()) == "child-model"
        # The child's write is copied away; the parent still sees its own.
        assert lc.get_last_resolved_model() == "parent-model"
    finally:
        lc._last_resolved_model.reset(token)


# ── complete() records what actually answered ─────────────────────────────────


class _Msg:
    def __init__(self, content: str | None, tool_calls: Any = None) -> None:
        self.content = content
        self.tool_calls = tool_calls


class _Choice:
    def __init__(self, message: _Msg) -> None:
        self.message = message


class _Usage:
    def __init__(self, prompt: int, completion: int) -> None:
        self.prompt_tokens = prompt
        self.completion_tokens = completion


class _Resp:
    def __init__(self, content: str | None, model: str, prompt: int = 10, completion: int = 5):
        self.choices = [_Choice(_Msg(content))]
        self.usage = _Usage(prompt, completion)
        self.model = model


class _OkBreaker:
    async def allow(self, group: str) -> bool:
        return True

    async def record_success(self, group: str) -> None:
        return None

    async def record_failure(self, group: str) -> None:
        return None


class _NoInflight:
    def acquire(self, group: str):
        return contextlib.nullcontext()


def _router_returning(response: _Resp):
    class _Router:
        async def acompletion(self, **kwargs: Any) -> _Resp:
            # The group the router was asked for; asserted by callers that care.
            _Router.seen_group = kwargs.get("model")
            return response

    _Router.seen_group = None
    return _Router()


async def test_complete_records_the_resolved_provider_slug_not_the_requested_group(
    monkeypatch,
):
    """The whole point: a fallback must be visible, not masked by the picker."""
    monkeypatch.setattr(lc, "charge_tokens", lambda _n: None)
    # Asked for `fast_chat`, but the provider that answered was a *different*
    # model -- exactly what a fallback produces.
    router = _router_returning(_Resp("hello", model="groq/openai/gpt-oss-120b"))
    service = lc.LiteLLMService(
        llm_router=router, circuit_breaker=_OkBreaker(), in_flight_limiter=_NoInflight()
    )

    out = await service.complete(
        messages=[{"role": "user", "content": "hi"}], model="fast_chat"
    )

    assert out["model"] == "groq/openai/gpt-oss-120b"
    assert lc.get_last_resolved_model() == "groq/openai/gpt-oss-120b"
    # Sanity: the router really was called through the requested group.
    assert router.seen_group == "fast_chat"


async def test_an_empty_response_does_not_overwrite_the_resolved_model(monkeypatch):
    """A discarded attempt must not be reported as the answer.

    `complete()` only records the slug once it accepts the text, so an empty
    first attempt followed by a real one reports the real one.
    """
    monkeypatch.setattr(lc, "charge_tokens", lambda _n: None)
    calls = {"n": 0}

    class _Router:
        seen_group: str | None = None

        async def acompletion(self, **kwargs: Any) -> _Resp:
            calls["n"] += 1
            _Router.seen_group = kwargs.get("model")
            if calls["n"] == 1:
                return _Resp(None, model="empty-attempt")
            return _Resp("real answer", model="the-answer-model")

    service = lc.LiteLLMService(
        llm_router=_Router(), circuit_breaker=_OkBreaker(), in_flight_limiter=_NoInflight()
    )

    out = await service.complete(
        messages=[{"role": "user", "content": "hi"}], model="fast_chat"
    )

    assert out["content"] == "real answer"
    assert lc.get_last_resolved_model() == "the-answer-model"
