"""The compaction funnel: trim -> replay -> bounded summary -> counted drop.

Before this, every message over the budget went into one summarisation call, on
every turn, with no cap. Two consequences were invisible in the tests that
existed: a long conversation paid for an ever-growing model call each turn, and
the summary's *input* could itself overflow the summariser's window — so the
mechanism meant to protect the context window could fail on its own input and,
because summarisation is fail-open, silently drop the history it existed to keep.

These tests pin the three stages and, importantly, the fact that the cheap stage
runs **without** a model call at all.
"""

from __future__ import annotations

import pytest
from backend.app.services.context_compiler import ContextCompiler


def _msg(role: str, chars: int, token_marker: str = "x") -> dict:
    return {"role": role, "content": token_marker * chars}


@pytest.fixture()
def compiler():
    # A small context limit keeps the arithmetic obvious and the test fast.
    comp = ContextCompiler(target_budget_ratio=0.75, reserve_completion_tokens=100)
    comp.get_max_context = lambda model: 4000  # type: ignore[assignment]
    return comp


def _summary_calls(monkeypatch) -> list:
    """Record any summarisation call. The funnel must not need one to trim."""
    from backend.app.services import context_compiler as module

    calls: list = []

    async def fake_completion(*args, **kwargs):
        calls.append(kwargs)
        return "summary"

    monkeypatch.setattr(module.ai_client, "completion", fake_completion)
    return calls


def test_trimming_replays_the_history_without_an_llm_call(compiler, monkeypatch):
    """Stage 1 is deterministic: if trimming fits the budget, no summary is bought."""
    calls = _summary_calls(monkeypatch)
    # 40 messages x 2000 chars, trimmed to 200 chars each -> comfortably in budget.
    overflow = [_msg("user", 2000, "a") for _ in range(40)]

    result = compiler.stage_overflow(
        overflow, system_prompt="sys", model="m", kept_tokens=0, per_message_chars=200
    )

    assert result["replay"], "trimmed history should be replayed verbatim"
    assert result["summary_input"] == []
    assert result["dropped"] == 0
    assert calls == [], "stage 1 must not call the model"
    assert all(len(m["content"]) <= 260 for m in result["replay"])


def test_trimming_preserves_the_head_of_each_message(compiler):
    """Identifiers, file names and constraints live at the head; keep them."""
    body = "INVOICE-4471 must stay verbatim. " + ("filler " * 500)
    result = compiler.stage_overflow(
        [{"role": "user", "content": body}],
        system_prompt="sys",
        model="m",
        kept_tokens=0,
        per_message_chars=80,
    )
    first = result["replay"][0]["content"] if result["replay"] else result["summary_input"][0]["content"]
    assert first.startswith("INVOICE-4471 must stay verbatim.")
    assert "chars trimmed" in first


def test_summary_input_is_bounded_and_the_dropped_count_is_reported(compiler):
    """Stage 2/3: a 200-message overflow must not become a 200-message LLM call."""
    overflow = [
        {"role": "user", "content": f"turn-{i} " + "b" * 2000} for i in range(200)
    ]

    result = compiler.stage_overflow(
        overflow,
        system_prompt="sys",
        model="m",
        kept_tokens=0,
        per_message_chars=4000,  # no trimming, so the bound has to do the work
        max_summary_messages=40,
    )

    assert result["replay"] == []
    assert len(result["summary_input"]) == 40
    assert result["dropped"] == 160, "the caller needs this to state what was omitted"
    # Newest is kept: the most recent context is the most likely to matter, so the
    # retained window must be turns 160..199 and must not include turn 0.
    assert result["summary_input"][-1]["content"].startswith("turn-199")
    assert result["summary_input"][0]["content"].startswith("turn-160")
    assert all(not m["content"].startswith("turn-0 ") for m in result["summary_input"])


def test_no_overflow_is_a_noop(compiler):
    assert compiler.stage_overflow(
        [], system_prompt="sys", model="m", kept_tokens=0
    ) == {"replay": [], "summary_input": [], "dropped": 0}


class _Msg:
    def __init__(self, content: str, msg_type: str = "human") -> None:
        self.content = content
        self.type = msg_type


class _NoCritic:
    async def evaluate(self, **kwargs):
        return {"approved": True, "critique": ""}


async def test_synthesizer_trims_overflow_instead_of_buying_a_summary(monkeypatch):
    """The wiring test: the *node* must use the funnel, not just the compiler.

    Reverting nodes.py to summarise every over-budget message leaves the
    ``stage_overflow`` unit tests green, because they call the method directly.
    This one drives the real node, so it fails if the funnel is bypassed.
    """
    from backend.app.agents.orchestrator import nodes
    from backend.app.services.confidence_service import confidence_service

    summarised: list = []
    sent: list = []

    async def fake_summary(messages, model="m"):
        summarised.append(messages)
        return "SUMMARY"

    async def fake_completion(**kwargs):
        sent.append(kwargs.get("messages"))
        return "An answer that cites the earlier turns."

    monkeypatch.setattr(nodes.context_compiler, "summarize_agent_history", fake_summary)
    monkeypatch.setattr(nodes.ai_client, "completion", fake_completion)
    monkeypatch.setattr(nodes, "critic_subagent", _NoCritic())
    monkeypatch.setattr(
        confidence_service,
        "decide",
        lambda *a, **k: {"confidence": 0.95, "action": "answer", "grounded": True},
    )

    # Long in characters, short in turns: over budget, but trimmable to fit.
    # Sizing matters: the default model's usable budget is ~92k tokens and
    # estimate_tokens is ~chars/2, so 8 x 40k chars (~160k tokens) genuinely
    # overflows while the 4k-char trims (~16k tokens) comfortably fit.
    history = [_Msg(f"MARKER-{i} " + "q" * 40000) for i in range(8)]
    history.append(_Msg("the live turn"))
    out = await nodes.synthesizer_node(
        {
            "messages": history,
            "user_id": "",
            "conversation_id": "c1",
            "trace_id": "",
            "mode": "normal",
            "system_prompt": "SYSTEM",
            "task_type": "general",
            "revision_count": 0,
            "citations": [],
        }
    )

    assert out["messages"][0].content
    assert summarised == [], "trimmable overflow must not cost a model call"

    # What the provider actually received: the trimmed history, identifiable by
    # the trim marker, with the head of each old message (where identifiers live)
    # intact -- not a summary block standing in for it.
    payload = str(sent[0])
    assert "chars trimmed" in payload, "overflow was not trimmed into the replay"
    assert "MARKER-1 " in payload, "the head of each old message must survive the trim"
    assert "[Earlier conversation summary]" not in payload

def test_usable_budget_matches_budget_history():
    """One formula, two callers. A drift here trims to a budget the splitter ignores."""
    comp = ContextCompiler(target_budget_ratio=0.75, reserve_completion_tokens=4096)
    comp.get_max_context = lambda model: 4000  # type: ignore[assignment]
    system = "s" * 400
    keep, compress, kept = comp.budget_history(
        [_msg("user", 400), _msg("user", 400)], system_prompt=system, model="m"
    )
    # Everything fits, so kept equals the real token cost of what was kept.
    assert compress == []
    assert kept >= comp.estimate_tokens(keep[0]["content"], "m")
