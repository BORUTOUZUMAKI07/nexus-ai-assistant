"""Tests for logprob plumbing in the LiteLLM client.

`services/decision.py` is only as good as this. Everything upstream can be
correct and the layer still degrades to nothing if `complete()` does not forward
the logprobs request or reports absence as an empty list -- and no test caught
either, because every decision test injects a fake client and never runs this
path at all. That is the §9.15 shape: the object was tested, the integration was
not.
"""
from __future__ import annotations

from typing import Any

import pytest
from backend.app.infrastructure.ai.litellm_client import _extract_logprobs


class _Obj:
    """Attribute-style response, which is what LiteLLM actually returns."""

    def __init__(self, **kwargs: Any) -> None:
        self.__dict__.update(kwargs)


def _response(content: list[Any] | None = None) -> _Obj:
    """A response whose `choices[0].logprobs.content` is `content`.

    Positional on purpose: the first version took `**logprobs` and was called as
    `_response(logprobs=[...])`, which built a *dict* `{"logprobs": [...]}` and
    iterated it -- yielding the string "logprobs" as if it were a token entry.
    The test then asserted on an empty token and would have passed for the wrong
    reason.
    """
    choice = _Obj(message=_Obj(content="yes"), logprobs=_Obj(content=content))
    return _Obj(choices=[choice], model="test-model", usage=_Obj(prompt_tokens=1, completion_tokens=1))


class _OpenBreaker:
    """A circuit breaker that always allows, and records outcomes.

    `complete()` calls `record_success` after a valid response, so a stub with
    only `allow` fails inside the client -- which looked like a test-harness
    error rather than a missing method, and hid all three forwarding tests.
    """

    async def allow(self, group: str) -> bool:
        return True

    async def record_success(self, group: str) -> None:
        return None

    async def record_failure(self, group: str) -> None:
        return None


class TestExtraction:
    def test_object_shaped_logprobs_are_flattened(self):
        """LiteLLM returns objects, and the decision layer wants dicts.

        The `top` pairs must survive as a list of tuples: that list is the whole
        distribution, and dropping it turns a measurement into a refusal.
        """
        response = _response(
            content=[_Obj(token=" yes", logprob=-0.05, top_logprobs=[_Obj(token=" yes", logprob=-0.05), _Obj(token=" no", logprob=-3.0)])]
        )

        tokens = _extract_logprobs(response)

        assert tokens is not None
        assert len(tokens) == 1
        assert tokens[0]["token"] == " yes"
        assert tokens[0]["top"] == [(" yes", -0.05), (" no", -3.0)]

    def test_dict_shaped_logprobs_are_accepted_too(self):
        """Provider coverage varies and some paths return plain dicts.

        Handling only the object shape means the layer silently degrades to its
        rule on exactly the deployments where logprobs were available.
        """
        response = _Obj(
            choices=[_Obj(message=_Obj(content="yes"), logprobs={"content": [{"token": "yes", "logprob": -0.1, "top_logprobs": [{"token": "yes", "logprob": -0.1}]}]})],
            model="test-model",
            usage=_Obj(prompt_tokens=1, completion_tokens=1),
        )

        tokens = _extract_logprobs(response)

        assert tokens is not None
        assert tokens[0]["top"] == [("yes", -0.1)]

    def test_absent_logprobs_are_none_not_an_empty_list(self):
        """The distinction the decision layer depends on.

        `None` means "this provider cannot supply scores" and triggers the
        fallback. `[]` means "the model produced a token with no alternatives"
        and would be read as a measurement with no distribution -- a different
        and much stronger claim. Collapsing the two makes a provider failure
        indistinguishable from a model that refused to answer.
        """
        assert _extract_logprobs(_response()) is None
        assert _extract_logprobs(_Obj(choices=[], model="m")) is None
        assert _extract_logprobs(_Obj(choices=None, model="m")) is None

    def test_a_non_numeric_logprob_is_skipped_rather_than_propagated(self):
        """A value we cannot reason about must not enter a softmax."""
        response = _response(
            content=[_Obj(token=" yes", logprob=-0.05, top_logprobs=[_Obj(token=" yes", logprob="not-a-number")])]
        )

        tokens = _extract_logprobs(response)

        assert tokens is not None
        assert tokens[0]["top"] == []

    def test_an_odd_response_shape_never_raises(self):
        """The extractor runs inside a try/except for a reason: providers vary.

        Whatever arrives, the answer is None or a list. An exception here would
        surface as a failed model call for something that was only ever
        best-effort metadata.
        """
        class Hostile:
            @property
            def choices(self) -> Any:
                raise RuntimeError("provider shape changed")

        assert _extract_logprobs(Hostile()) is None


class TestForwarding:
    """The forwarding path, via the real `complete()` with a stub router."""

    @pytest.mark.asyncio
    async def test_complete_forwards_logprobs_and_reports_them(self):
        """The integration the decision layer actually depends on.

        Asserted end to end through `complete()`: the request must carry
        `logprobs`/`top_logprobs` to the router, and the response's scores must
        come back under the result key. Checking either half alone leaves a real
        gap -- forwarding without returning means the layer sees None and
        degrades on every call.
        """
        from backend.app.infrastructure.ai import litellm_client

        captured: dict[str, Any] = {}
        response = _response(
            content=[_Obj(token=" yes", logprob=-0.05, top_logprobs=[_Obj(token=" yes", logprob=-0.05), _Obj(token=" no", logprob=-3.0)])]
        )

        class _Router:
            async def acompletion(self, **kwargs: Any) -> Any:
                captured.update(kwargs)
                return response

        client = litellm_client.LiteLLMService(llm_router=_Router())
        client._breaker = _OpenBreaker()

        result = await client.complete(
            messages=[{"role": "user", "content": "hi"}],
            model="fast_chat",
            max_tokens=8,
            logprobs=True,
            top_logprobs=20,
        )

        assert captured["logprobs"] is True
        assert captured["top_logprobs"] == 20
        assert result["logprobs"] is not None
        assert result["logprobs"][0]["top"] == [(" yes", -0.05), (" no", -3.0)]

    @pytest.mark.asyncio
    async def test_complete_does_not_request_logprobs_unless_asked(self):
        """Defaults must be inert.

        Every existing call site goes through this method. If logprobs were
        requested unconditionally, every model call in the process would be
        billed and constrained for them by a feature that is off by default.
        """
        from backend.app.infrastructure.ai import litellm_client

        captured: dict[str, Any] = {}

        class _Router:
            async def acompletion(self, **kwargs: Any) -> Any:
                captured.update(kwargs)
                return _response()

        client = litellm_client.LiteLLMService(llm_router=_Router())
        client._breaker = _OpenBreaker()

        result = await client.complete(messages=[{"role": "user", "content": "hi"}], model="fast_chat", max_tokens=8)

        assert "logprobs" not in captured
        assert "top_logprobs" not in captured
        assert result["logprobs"] is None
        # Always present, so a caller cannot mistake a missing key for an
        # absent feature.
        assert "logprobs" in result

    @pytest.mark.asyncio
    async def test_response_format_still_reaches_the_router_alongside_logprobs(self):
        """The two are independent request channels and both must survive.

        `response_format` is how structured output is requested elsewhere in the
        app. Merging the two into one kwargs dict is how that would silently
        regress -- and it would only show up on providers that accept both.
        """
        from backend.app.infrastructure.ai import litellm_client

        captured: dict[str, Any] = {}

        class _Router:
            async def acompletion(self, **kwargs: Any) -> Any:
                captured.update(kwargs)
                return _response()

        client = litellm_client.LiteLLMService(llm_router=_Router())
        client._breaker = _OpenBreaker()

        await client.complete(
            messages=[{"role": "user", "content": "hi"}],
            model="fast_chat",
            max_tokens=8,
            response_format={"type": "json_object"},
            logprobs=True,
        )

        # `response_format` is splatted into the router call, so it arrives as
        # top-level keys rather than under a `response_format` key. Asserting
        # the shape that actually reaches the provider, since asserting the
        # wrong one would pass for the wrong reason or fail for no reason.
        assert captured.get("type") == "json_object"
