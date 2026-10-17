"""P1 #12 -- structured output must feed the per-run spend meter.

``instructor.from_litellm(router.acompletion)`` wrapped the raw router, which
never calls ``charge_tokens`` (that lives in ``LiteLLMService.complete``), so
every structured call -- including the artifact classifier -- was invisible to
``AGENT_LOOP_TOKEN_BUDGET``.
"""
import ast
from pathlib import Path

import backend.app.services.structured_output as so
import pytest


@pytest.mark.asyncio
async def test_the_wrapper_charges_the_tokens_from_the_response(monkeypatch):
    charged: list[int] = []

    class _Usage:
        prompt_tokens = 11
        completion_tokens = 7

    class _Resp:
        usage = _Usage()

    async def _fake(**kwargs):
        return _Resp()

    monkeypatch.setattr(so.router, "acompletion", _fake)
    monkeypatch.setattr(so, "charge_tokens", charged.append)

    result = await so._metered_router_acompletion(model="fast_chat", messages=[])

    assert isinstance(result, _Resp)
    assert charged == [18]


@pytest.mark.asyncio
async def test_a_metering_failure_does_not_break_the_call(monkeypatch):
    class _Resp:
        usage = None

    async def _fake(**kwargs):
        return _Resp()

    def _boom(_n):
        raise RuntimeError("meter broken")

    monkeypatch.setattr(so.router, "acompletion", _fake)
    monkeypatch.setattr(so, "charge_tokens", _boom)

    assert isinstance(await so._metered_router_acompletion(model="fast_chat", messages=[]), _Resp)


def test_the_production_client_is_built_on_the_metered_wrapper():
    """A wiring check: ``from_litellm`` must be handed the wrapper, not the router.

    Reverting to ``router.acompletion`` restores the exact bypass this fixes, and
    no behavioural test can see it because the client is built at import time.
    """
    src = Path(so.__file__).read_text(encoding="utf-8")
    calls = [
        n
        for n in ast.walk(ast.parse(src))
        if isinstance(n, ast.Call)
        and isinstance(n.func, ast.Attribute)
        and n.func.attr == "from_litellm"
    ]
    assert calls, "structured output must build an instructor client"
    arg = calls[0].args[0]
    assert isinstance(arg, ast.Name) and arg.id == "_metered_router_acompletion", ast.dump(arg)
