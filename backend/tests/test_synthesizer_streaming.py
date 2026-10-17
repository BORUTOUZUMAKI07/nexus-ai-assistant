"""The synthesizer delivers its finished answer progressively -- after the
guardrails have run.

The property under test is not "does it stream" but "is what streams the same
string that is persisted, and has that string already been through PII
redaction". The raw token stream is deliberately *not* what reaches the client:
redaction and the confidence hedge both operate on the complete text, so a live
token stream would hand the user an un-redacted draft and make the transcript
disagree with the row ``run_executor`` writes. ``_emit_answer_stream`` is called
with the final text, and these tests pin that boundary -- the first one would
pass against raw streaming, the second is what forbids it.
"""
from __future__ import annotations

import pytest


class _Msg:
    def __init__(self, content: str, msg_type: str = "human") -> None:
        self.content = content
        self.type = msg_type


class _NoCritic:
    async def evaluate(self, **kwargs):
        return {"approved": True, "critique": ""}


def _state(user_text: str = "Tell me about X"):
    return {
        "messages": [_Msg(user_text)],
        "user_id": "",
        "conversation_id": "c1",
        "trace_id": "",
        "mode": "normal",
        "system_prompt": "SYSTEM",
        "task_type": "general",
        "revision_count": 0,
        "citations": [],
    }


@pytest.fixture
def synth_env(monkeypatch):
    """The synthesizer's collaborators, minus the model call.

    ``critic_subagent`` and the confidence gate are replaced with deterministic
    stubs; the model call itself is set per test through
    ``nodes.ai_client.completion``.
    """
    from backend.app.agents.orchestrator import nodes
    from backend.app.services.confidence_service import confidence_service

    monkeypatch.setattr(nodes, "critic_subagent", _NoCritic())
    monkeypatch.setattr(
        confidence_service,
        "decide",
        lambda *a, **k: {
            "confidence": 0.95,
            "action": "answer",
            "grounded": True,
            "threshold": 0.6,
        },
    )
    return nodes


def _recorder():
    pieces: list[dict] = []

    def writer(payload):
        pieces.append(payload)

    return pieces, writer


@pytest.mark.asyncio
async def test_the_streamed_pieces_concatenate_to_the_persisted_answer(monkeypatch, synth_env):
    nodes = synth_env

    async def fake_completion(**kwargs):
        return (
            "A first sentence. A second sentence that makes the answer long "
            "enough to be split into more than one streamed piece."
        )

    monkeypatch.setattr(nodes.ai_client, "completion", fake_completion)
    pieces, writer = _recorder()
    monkeypatch.setattr(nodes, "get_stream_writer", lambda: writer)

    out = await nodes.synthesizer_node(_state())

    content = out["messages"][0].content
    assert len(pieces) > 1, "the answer arrived in one piece, so nothing streams"
    assert "".join(p["content"] for p in pieces) == content
    assert {p["type"] for p in pieces} == {"text_delta"}


@pytest.mark.asyncio
async def test_the_guardrail_runs_before_anything_is_streamed(monkeypatch, synth_env):
    """Raw streaming would let this draft reach the user. Dripping the final
    text is safe *because* the redaction already happened."""
    nodes = synth_env

    async def fake_completion(**kwargs):
        return "Contact the owner at jane.doe@example.com to request access."

    monkeypatch.setattr(nodes.ai_client, "completion", fake_completion)
    pieces, writer = _recorder()
    monkeypatch.setattr(nodes, "get_stream_writer", lambda: writer)

    out = await nodes.synthesizer_node(_state())

    streamed = "".join(p["content"] for p in pieces)
    assert "EMAIL_REDACTED" in streamed
    assert "jane.doe@example.com" not in streamed
    assert "jane.doe@example.com" not in out["messages"][0].content
    assert streamed == out["messages"][0].content


@pytest.mark.asyncio
async def test_no_stream_writer_is_not_an_error(monkeypatch, synth_env):
    """A direct call (no runnable context) must behave exactly as before."""
    nodes = synth_env

    async def fake_completion(**kwargs):
        return "Just an answer."

    def boom():
        raise RuntimeError("not running inside a graph")

    monkeypatch.setattr(nodes.ai_client, "completion", fake_completion)
    monkeypatch.setattr(nodes, "get_stream_writer", boom)

    out = await nodes.synthesizer_node(_state())
    assert out["messages"][0].content == "Just an answer."
