"""`stream_options.include_usage` must follow provider capability.

Sending it unconditionally is not harmless: OpenAI-compatible providers use it
to return aggregated usage on the final chunk, but providers whose param list
does not include ``stream_options`` can reject the request or silently drop the
parameter. The spend meter then falls back to a chars/4 estimate while the logs
claim usage was reported. Gate the parameter on LiteLLM's own capability table
and fail closed when that table cannot be read.
"""
from __future__ import annotations

from typing import Any

import pytest
from backend.app.infrastructure.ai import litellm_client as lc
from backend.app.infrastructure.resilience.guards import CircuitBreaker, InFlightLimiter


class _Delta:
    content = "hi"


class _Choice:
    delta = _Delta()


class _Chunk:
    choices = [_Choice()]
    usage = None


class _FakeStream:
    def __aiter__(self):
        async def _chunks():
            yield _Chunk()

        return _chunks()

    async def aclose(self):
        return None


class _CapturingRouter:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def acompletion(self, **kwargs):
        self.calls.append(kwargs)
        return _FakeStream()


def _service(router: _CapturingRouter) -> lc.LiteLLMService:
    return lc.LiteLLMService(
        llm_router=router,
        circuit_breaker=CircuitBreaker(failure_threshold=99, open_timeout_seconds=1.0),
        in_flight_limiter=InFlightLimiter(max_in_flight=4),
    )


def _isolate_spend(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(lc, "charge_tokens", lambda _n: None)
    monkeypatch.setattr(lc, "record_provider_usage", lambda **_kwargs: None)


@pytest.mark.asyncio
async def test_supported_provider_gets_include_usage(monkeypatch):
    monkeypatch.setattr(
        lc.litellm, "get_supported_openai_params", lambda **_kwargs: ["stream", "stream_options"]
    )
    _isolate_spend(monkeypatch)
    router = _CapturingRouter()

    await _stream(_service(router), "fast_chat")

    assert router.calls[0]["stream_options"] == {"include_usage": True}


@pytest.mark.asyncio
async def test_unsupported_provider_does_not_get_include_usage(monkeypatch):
    monkeypatch.setattr(
        lc.litellm, "get_supported_openai_params", lambda **_kwargs: ["stream", "temperature"]
    )
    _isolate_spend(monkeypatch)
    router = _CapturingRouter()

    await _stream(_service(router), "vision_analysis")

    assert "stream_options" not in router.calls[0]


@pytest.mark.asyncio
@pytest.mark.parametrize("probe_result", [None, [], RuntimeError("capability table down")])
@pytest.mark.parametrize("model", ["fast_chat", "vision_analysis"])
async def test_unknown_or_failed_capability_probe_fails_closed(
    monkeypatch, probe_result, model
):
    def _probe(**_kwargs):
        if isinstance(probe_result, Exception):
            raise probe_result
        return probe_result

    monkeypatch.setattr(lc.litellm, "get_supported_openai_params", _probe)
    _isolate_spend(monkeypatch)
    router = _CapturingRouter()

    await _stream(_service(router), model)

    assert "stream_options" not in router.calls[0]


@pytest.mark.asyncio
async def test_capability_probe_reads_the_group_primary_model(monkeypatch):
    probed: list[str] = []

    def _probe(*, model: str, **_kwargs):
        probed.append(model)
        return ["stream_options"]

    monkeypatch.setattr(lc.litellm, "get_supported_openai_params", _probe)
    _isolate_spend(monkeypatch)
    router = _CapturingRouter()

    await _stream(_service(router), "vision_analysis")

    assert probed == ["gemini/gemini-3.5-flash"]


async def _stream(service: lc.LiteLLMService, model: str) -> None:
    chunks = [
        chunk
        async for chunk in service.astream(messages=[{"role": "user", "content": "hi"}], model=model)
    ]
    assert chunks == ["hi"]
