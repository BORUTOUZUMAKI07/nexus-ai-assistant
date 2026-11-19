"""A provider outage at synthesis time must cost a revision, never the turn.

The synthesizer is the last node and is deliberately NO_RETRY (re-synthesising
an entire answer is expensive). Its three ``ai_client.completion`` calls were
the only unguarded model calls in an otherwise fail-open node: when every model
group failed, the exception escaped the node, the run aborted with a bare error
frame and no assistant message, and the client saw nothing.

These tests pin the survivable behaviour:
  * with no evidence, still return a clear, honest degradation answer;
  * with research findings, relay the researcher's real, sourced answer;
  * with a draft already in hand (critic loop), keep that draft.
"""
from __future__ import annotations

import pytest
from backend.app.agents.orchestrator import nodes
from langchain_core.messages import HumanMessage


def _state(**overrides):
    base = {
        "messages": [HumanMessage(content="answer this question")],
        "system_prompt": "You are Nexus AI.",
        "user_id": "user-1",
    }
    base.update(overrides)
    return base


def _completion_down(*_args, **_kwargs):
    raise RuntimeError("All model groups failed")


async def _critic_approves(*_args, **_kwargs):
    return {"approved": True, "critique": ""}


async def _critic_rejects(*_args, **_kwargs):
    return {"approved": False, "critique": "be more specific"}


@pytest.fixture(autouse=True)
def _harness(monkeypatch):
    """Keep every non-provider dependency deterministic and local."""
    monkeypatch.setattr(nodes, "_emit_answer_stream", _noop_emit)
    monkeypatch.setattr(nodes.critic_subagent, "evaluate", _critic_approves)


async def _noop_emit(*_args, **_kwargs):
    return None


@pytest.mark.asyncio
async def test_primary_draft_outage_still_returns_an_answer(monkeypatch):
    monkeypatch.setattr(nodes.ai_client, "completion", _completion_down)

    update = await nodes.synthesizer_node(_state())

    answer = update["messages"][0].content
    assert isinstance(answer, str) and answer.strip()
    # No tool/evidence was available, so the honest outcome is a graceful
    # "try again" -- never a crashed run with no message at all.
    assert "try again" in answer.lower()


@pytest.mark.asyncio
async def test_primary_draft_outage_relays_research_findings(monkeypatch):
    monkeypatch.setattr(nodes.ai_client, "completion", _completion_down)
    findings = "Acme raised $5M at a $50M valuation (https://example.com/news)."

    update = await nodes.synthesizer_node(
        _state(subagent_outputs={"researcher": {"synthesis": findings}})
    )

    assert update["messages"][0].content == findings


@pytest.mark.asyncio
async def test_revision_draft_outage_keeps_the_earlier_draft(monkeypatch):
    calls = 0

    async def _completion_flaky(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            return "The first, complete draft."
        raise RuntimeError("provider down on revision")

    monkeypatch.setattr(nodes.ai_client, "completion", _completion_flaky)
    monkeypatch.setattr(nodes.critic_subagent, "evaluate", _critic_rejects)

    update = await nodes.synthesizer_node(_state(task_type="research"))

    assert update["messages"][0].content == "The first, complete draft."
