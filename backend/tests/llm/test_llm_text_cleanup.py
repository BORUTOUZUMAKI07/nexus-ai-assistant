"""Model protocol markup must never reach the user as an answer.

Regression origin: a live run of the agentic synthesizer answered a plain
question with a raw tool call written as text --

    <|tool_call_start|>[google(query='why does the sky appear blue'), ...]<|tool_call_end|>

-- because the provider fallback chain had degraded onto
``liquid/lfm-2.5-2.6b:free`` and that model returns its tool call on the text
channel. The blob was delivered verbatim. ``strip_model_artifacts`` is applied
at the single content-extraction boundary in ``LiteLLMService.complete`` so
every text consumer is protected and a markup-only response is treated as empty.

These tests run against in-memory fakes: no network, no keys, no database.
"""
from types import SimpleNamespace

import pytest
from backend.app.infrastructure.ai.litellm_client import LiteLLMService
from backend.app.infrastructure.resilience.guards import CircuitBreaker, InFlightLimiter
from backend.app.services.llm_text_cleanup import strip_model_artifacts

# The exact shape that reached the user, kept verbatim so this test fails if the
# span pattern is loosened.
LIQUID_TOOL_CALL = (
    "<|tool_call_start|>[google(query='why does the sky appear blue explanation "
    "Rayleigh scattering'), google(query='sky color science Rayleigh scattering "
    "atmospheric physics')]<|tool_call_end|>"
)


# ── the pure scrubber ────────────────────────────────────────────────────────


def test_a_liquid_tool_call_span_is_removed_entirely():
    assert strip_model_artifacts(LIQUID_TOOL_CALL) == ""


def test_prose_around_a_tool_call_survives():
    text = f"The sky is blue because of Rayleigh scattering.\n\n{LIQUID_TOOL_CALL}"
    assert strip_model_artifacts(text) == "The sky is blue because of Rayleigh scattering."


def test_xml_style_tool_call_block_is_removed():
    text = 'Here you go.\n<tool_call>{"name": "search", "arguments": {}}</tool_call>'
    assert strip_model_artifacts(text) == "Here you go."


def test_stray_control_tokens_are_removed_but_words_remain():
    text = "<|im_start|>assistant\nSunlight scatters in the atmosphere.<|eot_id|>"
    assert strip_model_artifacts(text) == "assistant\nSunlight scatters in the atmosphere."


def test_an_unclosed_tool_call_start_drops_the_dangling_arguments():
    text = "Intro.\n<|tool_call_start|>[google(query='x')]"
    assert strip_model_artifacts(text) == "Intro."


def test_plain_prose_is_untouched():
    text = "A simple answer.\n\nWith a second paragraph and `inline code`."
    assert strip_model_artifacts(text) == text


def test_angle_brackets_in_normal_text_are_not_treated_as_tokens():
    # A comparison, an XML tag and a generic type have no `<|...|>` pair and no
    # tool-call wrapper; a protocol scrubber must leave them alone.
    text = "If a < b and b > c, wrap it in <div> and type List<int>."
    assert strip_model_artifacts(text) == text


def test_empty_input_is_returned_unchanged():
    assert strip_model_artifacts("") == ""


# ── the boundary that calls it ───────────────────────────────────────────────


class _FakeResponse:
    def __init__(self, content, model="fake-model"):
        self.model = model
        self.usage = SimpleNamespace(prompt_tokens=5, completion_tokens=2)
        self.choices = [
            SimpleNamespace(message=SimpleNamespace(content=content, tool_calls=None))
        ]


class _MarkupThenAnswerRouter:
    """`fast_chat` answers only with tool-call markup; `large_context` answers."""

    def __init__(self):
        self.calls: list[str] = []

    async def acompletion(self, model=None, **kwargs):
        self.calls.append(model)
        if model == "fast_chat":
            return _FakeResponse(LIQUID_TOOL_CALL, model="resolved/liquid")
        return _FakeResponse("Sunlight scatters off air molecules.", model=f"resolved/{model}")


@pytest.mark.asyncio
async def test_markup_only_response_is_rejected_and_falls_back():
    router = _MarkupThenAnswerRouter()
    svc = LiteLLMService(
        llm_router=router,
        circuit_breaker=CircuitBreaker(),
        in_flight_limiter=InFlightLimiter(8),
    )

    res = await svc.complete([{"role": "user", "content": "why is the sky blue"}], model="fast_chat")

    # The markup group was tried and rejected; the next group produced the answer.
    assert router.calls[:2] == ["fast_chat", "large_context"]
    assert res["content"] == "Sunlight scatters off air molecules."
    assert "<|" not in res["content"]


@pytest.mark.asyncio
async def test_clean_content_is_returned_without_falling_back():
    router = _MarkupThenAnswerRouter()
    svc = LiteLLMService(
        llm_router=router,
        circuit_breaker=CircuitBreaker(),
        in_flight_limiter=InFlightLimiter(8),
    )

    res = await svc.complete([{"role": "user", "content": "hi"}], model="large_context")

    assert router.calls == ["large_context"]
    assert res["content"] == "Sunlight scatters off air molecules."
