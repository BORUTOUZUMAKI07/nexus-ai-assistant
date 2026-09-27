"""
Unit tests for schema-enforced structured output with retry-with-repair
in the plan-service planner (JSON schema first, repair pass second, heuristic last).
"""
import json

import pytest
from backend.app.services import plan_service as plan_module


async def _fake_completion(sequence, captured=None):
    """Monkeypatched ai_client.completion returning scripted raw strings."""

    async def completion(messages, **kwargs):
        if captured is not None:
            captured.append({"messages": messages, "kwargs": kwargs})
        raw = sequence.pop(0) if sequence else ""
        return raw

    return completion


_VALID_PLAN = json.dumps(
    {"title": "Build Thing", "summary": "one sentence", "steps": ["Step one", "Step two"]}
)


@pytest.mark.asyncio
async def test_repair_pass_succeeds_after_unparseable_first_attempt(monkeypatch):
    captured = []
    monkeypatch.setattr(
        plan_module.ai_client,
        "completion",
        await _fake_completion(["this is not json at all", _VALID_PLAN], captured),
    )

    title, summary, steps = await plan_module._default_planner("Build Thing", [])

    assert title == "Build Thing"
    assert summary == "one sentence"
    assert steps == ["Step one", "Step two"]
    assert len(captured) == 2
    # Both calls request the provider-level json_object schema.
    for call in captured:
        assert call["kwargs"].get("response_format") == {"type": "json_object"}
    # The repair call feeds the failure context back to the model.
    last_user = captured[-1]["messages"][-1]["content"]
    assert "did not parse" in last_user


@pytest.mark.asyncio
async def test_heuristic_fallback_after_two_failures(monkeypatch):
    monkeypatch.setattr(
        plan_module.ai_client,
        "completion",
        await _fake_completion(["garbage", "more garbage"]),
    )

    title, summary, steps = await plan_module._default_planner(
        "Research quantum computing. Summarize findings. Also draft a plan.", []
    )

    assert title  # heuristic title derived offline
    assert "Heuristic plan" in (summary or "")
    assert len(steps) >= 2


@pytest.mark.asyncio
async def test_llm_exception_falls_back_to_heuristic(monkeypatch):
    async def explode(messages, **kwargs):
        raise RuntimeError("provider down")

    monkeypatch.setattr(plan_module.ai_client, "completion", explode)
    title, _, steps = await plan_module._default_planner("Build a thing.", [])
    assert title == "Build a thing."
    assert steps
