"""Model-visible means logged.

The checkpoint has always held the transcript, but not the *assembly*: which
memories, research findings and episodic summary were spliced into the system
prompt, and which slice of history survived compaction. Diagnosing a live
answer that contradicted its own research meant rebuilding a checkpoint blob by
hand, because nothing recorded what the synthesizer actually sent.

These tests pin the snapshot: it is built from the final assembly, it always
keeps the system prompt, it is capped, and it reaches the node's return value so
the checkpointer persists it.
"""

from __future__ import annotations

import hashlib

from backend.app.agents.orchestrator.nodes import (
    _PROMPT_SNAPSHOT_MAX_CHARS,
    _build_prompt_snapshot,
)


def _m(role: str, content: str) -> dict:
    return {"role": role, "content": content}


def test_snapshot_records_the_system_prompt_and_its_hash():
    system = "You are Nexus. Answer from the research findings."
    snap = _build_prompt_snapshot(
        [_m("system", system), _m("user", "hi"), _m("assistant", "hello")], "large_context"
    )

    assert snap["model"] == "large_context"
    assert snap["message_count"] == 3
    assert snap["system_chars"] == len(system)
    assert snap["system_sha256"] == hashlib.sha256(system.encode("utf-8")).hexdigest()
    assert snap["messages"][0] == {"role": "system", "content": system}
    assert snap["truncated"] is False


def test_the_system_prompt_survives_the_cap_even_when_everything_else_is_cut():
    """Losing the system message would defeat the purpose: that is the assembly."""
    system = "S" * 500
    history = [_m("user", "x" * 5000) for _ in range(20)]

    snap = _build_prompt_snapshot([_m("system", system), *history], "fast_chat")

    assert snap["truncated"] is True
    assert snap["messages"][0]["content"] == system
    assert snap["total_chars"] > sum(len(m["content"]) for m in snap["messages"])


def test_the_cap_is_respected():
    history = [_m("user", "y" * 3000) for _ in range(20)]
    snap = _build_prompt_snapshot(history, "fast_chat")
    kept_chars = sum(len(m["content"]) for m in snap["messages"])
    assert kept_chars <= _PROMPT_SNAPSHOT_MAX_CHARS
    assert snap["truncated"] is True


def test_the_newest_messages_are_the_ones_kept():
    history = [_m("user", f"turn-{i} " + "z" * 3000) for i in range(20)]
    snap = _build_prompt_snapshot(history, "fast_chat")
    kept = [m["content"] for m in snap["messages"]]
    assert any(c.startswith("turn-19") for c in kept), "the live tail must survive"
    assert not any(c.startswith("turn-0 ") for c in kept), "the oldest goes first"


def test_an_empty_assembly_is_still_safe():
    snap = _build_prompt_snapshot([], "fast_chat")
    assert snap["message_count"] == 0
    assert snap["messages"] == []
    assert snap["system_chars"] == 0


# ── the node publishes it ────────────────────────────────────────────────────


class _Msg:
    def __init__(self, content: str, msg_type: str = "human") -> None:
        self.content = content
        self.type = msg_type


class _NoCritic:
    async def evaluate(self, **kwargs):
        return {"approved": True, "critique": ""}


async def test_synthesizer_returns_the_snapshot_so_the_checkpointer_persists_it(monkeypatch):
    from backend.app.agents.orchestrator import nodes
    from backend.app.services.confidence_service import confidence_service

    async def fake_completion(**kwargs):
        return "A perfectly ordinary answer."

    monkeypatch.setattr(nodes.ai_client, "completion", fake_completion)
    monkeypatch.setattr(nodes, "critic_subagent", _NoCritic())
    monkeypatch.setattr(
        confidence_service,
        "decide",
        lambda *a, **k: {"confidence": 0.95, "action": "answer", "grounded": True},
    )

    out = await nodes.synthesizer_node(
        {
            "messages": [_Msg("What is the capital of France?")],
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

    snap = out["prompt_snapshot"]
    assert snap["message_count"] >= 1

    # The synthesizer augments the system prompt (skills, research directive,
    # guardrails), so the hash is of the FINAL assembly rather than of the input.
    # The invariant that matters is that the hash describes the text that was
    # recorded -- otherwise a reader cannot tell which prompt the hash belongs to.
    system_text = snap["messages"][0]["content"]
    assert snap["messages"][0]["role"] == "system"
    assert hashlib.sha256(system_text.encode("utf-8")).hexdigest() == snap["system_sha256"]
    assert system_text.startswith("SYSTEM"), "the caller's prompt must be the base"
    assert snap["system_chars"] == len(system_text)
