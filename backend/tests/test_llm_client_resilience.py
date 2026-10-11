"""P1 #13 and #33 -- fallback billing and the streaming circuit breaker.

#13: the fallback chain bills the provider for every attempt, but ``charge_tokens``
was called once with only the winning attempt's tokens, so the wasted attempts
were invisible to ``AGENT_LOOP_TOKEN_BUDGET``.

#33: the breaker was told ``record_success`` the moment a stream was *accepted*,
so a stream that then died mid-flight, or produced nothing at all, left the
breaker closed.
"""
import contextlib

import pytest

from backend.app.infrastructure.ai import litellm_client as lc


class _OkBreaker:
    def __init__(self):
        self.events: list[tuple[str, str]] = []

    async def allow(self, group):
        return True

    async def record_success(self, group):
        self.events.append(("ok", group))

    async def record_failure(self, group):
        self.events.append(("fail", group))


class _NoInflight:
    def acquire(self, group):
        return contextlib.nullcontext()


# ── response doubles ──────────────────────────────────────────────────────────


class _Msg:
    def __init__(self, content, tool_calls=None):
        self.content = content
        self.tool_calls = tool_calls


class _Choice:
    def __init__(self, message):
        self.message = message


class _Usage:
    def __init__(self, prompt, completion):
        self.prompt_tokens = prompt
        self.completion_tokens = completion


class _Resp:
    def __init__(self, content, prompt=0, completion=0):
        self.choices = [_Choice(_Msg(content))]
        self.usage = _Usage(prompt, completion)
        self.model = "m"


# ── #13 ───────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_discarded_fallback_attempts_are_charged(monkeypatch):
    charged: list[int] = []
    monkeypatch.setattr(lc, "charge_tokens", charged.append)

    calls = {"n": 0}

    class _Router:
        async def acompletion(self, **kwargs):
            calls["n"] += 1
            if calls["n"] == 1:
                return _Resp(content=None, prompt=100, completion=50)  # empty, billed
            return _Resp(content="ok", prompt=10, completion=5)

    svc = lc.LiteLLMService(llm_router=_Router(), circuit_breaker=_OkBreaker(), in_flight_limiter=_NoInflight())
    res = await svc.complete(messages=[{"role": "user", "content": "x"}], model="fast_chat")

    assert res["content"] == "ok"
    # 150 tokens the provider billed for the empty attempt + 15 for the answer.
    assert charged == [165], charged


# ── #33 ───────────────────────────────────────────────────────────────────────


class _Delta:
    def __init__(self, content):
        self.content = content


class _StreamChoice:
    def __init__(self, content):
        self.delta = _Delta(content)


class _Chunk:
    def __init__(self, content):
        self.choices = [_StreamChoice(content)]


def _stream(gen):
    class _S:
        model = "m"

        def __aiter__(self):
            return gen()

        async def aclose(self):
            return None

    return _S()


@pytest.mark.asyncio
async def test_a_stream_that_produces_nothing_records_a_failure(monkeypatch):
    monkeypatch.setattr(lc, "charge_tokens", lambda _n: None)
    breaker = _OkBreaker()

    async def _empty():
        if False:  # pragma: no cover - makes this an async generator
            yield _Chunk("x")

    class _Router:
        async def acompletion(self, **kwargs):
            return _stream(_empty)

    svc = lc.LiteLLMService(llm_router=_Router(), circuit_breaker=breaker, in_flight_limiter=_NoInflight())
    out = [p async for p in svc.astream(messages=[{"role": "user", "content": "x"}], model="fast_chat")]

    assert out == []
    assert ("fail", "fast_chat") in breaker.events


@pytest.mark.asyncio
async def test_a_stream_that_dies_mid_flight_records_a_failure(monkeypatch):
    monkeypatch.setattr(lc, "charge_tokens", lambda _n: None)
    breaker = _OkBreaker()

    async def _dying():
        yield _Chunk("a")
        raise RuntimeError("connection dropped")

    class _Router:
        async def acompletion(self, **kwargs):
            return _stream(_dying)

    svc = lc.LiteLLMService(llm_router=_Router(), circuit_breaker=breaker, in_flight_limiter=_NoInflight())
    with pytest.raises(RuntimeError, match="connection dropped"):
        async for _ in svc.astream(messages=[{"role": "user", "content": "x"}], model="fast_chat"):
            pass

    assert ("fail", "fast_chat") in breaker.events


@pytest.mark.asyncio
async def test_a_healthy_stream_records_success(monkeypatch):
    monkeypatch.setattr(lc, "charge_tokens", lambda _n: None)
    breaker = _OkBreaker()

    async def _good():
        yield _Chunk("hello")

    class _Router:
        async def acompletion(self, **kwargs):
            return _stream(_good)

    svc = lc.LiteLLMService(llm_router=_Router(), circuit_breaker=breaker, in_flight_limiter=_NoInflight())
    out = [p async for p in svc.astream(messages=[{"role": "user", "content": "x"}], model="fast_chat")]

    assert out == ["hello"]
    assert ("ok", "fast_chat") in breaker.events
    assert ("fail", "fast_chat") not in breaker.events
