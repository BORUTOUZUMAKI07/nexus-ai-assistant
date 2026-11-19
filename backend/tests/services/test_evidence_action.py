"""The evidence gate must act on a failed verdict, not just wear a badge.

``evidence_gate.verify_evidence_support`` returns a verdict that the synthesizer
used to compute, log, return in graph state, stream to the UI as a "Low evidence"
pill -- and then deliver the answer unchanged. A calibrated gate that does not
gate is decorative. These tests pin a deterministic, fail-open action: a real
failed verdict qualifies the answer so an ungrounded claim is not presented as
verified; an un-evaluated, passing, or malformed verdict leaves it untouched.
"""
from __future__ import annotations

import pytest
from backend.app.services.evidence.action import (
    EvidenceActionResult,
    apply_evidence_action,
)


def _failed(confidence: float = 0.3) -> dict:
    return {
        "passed_gate": False,
        "confidence_score": confidence,
        "citations": [],
        "reason": f"Factual confidence ({confidence:.2f}) below threshold (0.70).",
    }


def test_failed_verdict_qualifies_the_answer():
    result = apply_evidence_action("Acme's revenue was $5M last year.", _failed())

    assert isinstance(result, EvidenceActionResult)
    assert result.acted is True
    assert result.confidence == 0.3
    assert "low evidence" in result.text.lower()
    assert "Acme's revenue was $5M last year." in result.text


def test_passing_verdict_leaves_the_answer_untouched():
    verdict = {
        "passed_gate": True,
        "confidence_score": 0.92,
        "citations": [],
        "reason": "Sufficient empirical context backing generated claims.",
    }

    result = apply_evidence_action("Acme's revenue was $5M [1].", verdict)

    assert result.acted is False
    assert result.text == "Acme's revenue was $5M [1]."


def test_gate_not_evaluated_leaves_the_answer_untouched():
    verdict = {
        "passed_gate": None,
        "confidence_score": 0.0,
        "citations": [],
        "reason": "No evidence sources available; gate not evaluated.",
    }

    result = apply_evidence_action("Hello there.", verdict)

    assert result.acted is False
    assert result.text == "Hello there."


@pytest.mark.parametrize("verdict", [None, {}, {"passed_gate": "false"}])
def test_malformed_verdict_fails_open(verdict):
    result = apply_evidence_action("An answer.", verdict)

    assert result.acted is False
    assert result.text == "An answer."


def test_does_not_stack_a_second_caveat_on_an_already_hedged_answer():
    hedged = (
        "*Low confidence (40%) — the sources I found only partially support "
        "this. Worth verifying before you rely on it.*\n\nAn answer."
    )

    result = apply_evidence_action(hedged, _failed())

    assert result.acted is False
    assert result.text == hedged


def test_a_question_is_not_a_factual_claim_to_qualify():
    result = apply_evidence_action("What is Acme's revenue?", _failed())

    assert result.acted is False
    assert result.text == "What is Acme's revenue?"


# ── The synthesizer must call the action, not merely carry the badge ──────────


@pytest.mark.asyncio
async def test_synthesizer_qualifies_an_answer_the_gate_found_ungrounded(monkeypatch):
    from backend.app.agents.orchestrator import nodes
    from langchain_core.messages import HumanMessage

    async def _completion(*_args, **_kwargs):
        return "Acme's revenue was $5M last year."

    monkeypatch.setattr(nodes.ai_client, "completion", _completion)
    monkeypatch.setattr(nodes, "_emit_answer_stream", lambda *_a, **_k: _noop())
    monkeypatch.setattr(
        nodes.evidence_gate,
        "verify_evidence_support",
        lambda _text, _contexts: _failed(0.3),
    )
    state = {
        "messages": [HumanMessage(content="What was Acme's revenue?")],
        "system_prompt": "You are Nexus AI.",
        "user_id": "user-1",
        "citations": [
            {
                "file_id": "00000000-0000-0000-0000-000000000000",
                "filename": "report.pdf",
                "chunk_index": 0,
                "score": 0.9,
                "content_snippet": "Acme reported revenue of $5M last year.",
                "metadata": {},
            }
        ],
    }

    update = await nodes.synthesizer_node(state)

    answer = update["messages"][0].content
    assert "low evidence" in answer.lower()
    assert "Acme's revenue was $5M last year." in answer
    # The gate verdict is still reported -- the action changes what is shown,
    # not the evidence that was measured.
    assert update["evidence_gate_passed"] is False


async def _noop():
    return None


@pytest.mark.asyncio
async def test_synthesizer_does_not_qualify_a_grounded_answer(monkeypatch):
    from backend.app.agents.orchestrator import nodes
    from langchain_core.messages import HumanMessage

    async def _completion(*_args, **_kwargs):
        return "Acme's revenue was $5M [1]."

    passing = {
        "passed_gate": True,
        "confidence_score": 0.95,
        "citations": [],
        "reason": "Sufficient empirical context backing generated claims.",
    }
    monkeypatch.setattr(nodes.ai_client, "completion", _completion)
    monkeypatch.setattr(nodes, "_emit_answer_stream", lambda *_a, **_k: _noop())
    monkeypatch.setattr(
        nodes.evidence_gate,
        "verify_evidence_support",
        lambda _text, _contexts: passing,
    )
    state = {
        "messages": [HumanMessage(content="What was Acme's revenue?")],
        "system_prompt": "You are Nexus AI.",
        "user_id": "user-1",
    }

    update = await nodes.synthesizer_node(state)

    assert update["messages"][0].content == "Acme's revenue was $5M [1]."
