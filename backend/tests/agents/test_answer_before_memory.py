"""The client must see the answer before the best-effort memory write.

`_emit_answer_stream` drips the final text to the client in bounded frames. The
mem0 extraction + local mirror that followed the model call were awaited
*before* that emission: an embedding, a vector write and a second DB session
all sat between "answer is ready" and "answer is visible". Those operations are
best-effort (the code already fails open), so they must not tax time-to-first-
token. This pins the ordering, not just the presence of both calls.
"""
from __future__ import annotations

import pytest


class _Msg:
    def __init__(self, content: str, msg_type: str = "human") -> None:
        self.content = content
        self.type = msg_type


class _NoCritic:
    async def evaluate(self, **_kwargs):
        return {"approved": True, "critique": ""}


@pytest.mark.asyncio
async def test_answer_is_emitted_before_the_memory_write(monkeypatch):
    from backend.app.agents.orchestrator import nodes

    order: list[str] = []

    async def _completion(**_kwargs):
        return "I have saved your preference."

    async def _emit(text: str) -> None:
        order.append("emit")
        assert text

    async def _add(**_kwargs):
        order.append("memory")
        return [{"id": "m1", "memory": "User prefers dark mode"}]

    async def _mirror(**_kwargs):
        return 1

    async def _noop(*_args, **_kwargs):
        return None

    monkeypatch.setattr(nodes.ai_client, "completion", _completion)
    monkeypatch.setattr(nodes, "critic_subagent", _NoCritic())
    monkeypatch.setattr(nodes, "_emit_answer_stream", _emit)
    monkeypatch.setattr(nodes.long_term_memory, "add_from_conversation", _add)
    monkeypatch.setattr(nodes, "_mirror_memories_to_table", _mirror)
    monkeypatch.setattr(nodes, "_reinforce_memories_background", _noop)

    state = {
        "messages": [_Msg("I prefer dark mode in all my tools")],
        "user_id": "u1",
        "conversation_id": "c1",
        "trace_id": "",
        "mode": "normal",
        "system_prompt": "SYSTEM",
        "task_type": "general",
        "revision_count": 0,
        "citations": [],
    }

    await nodes.synthesizer_node(state)

    assert order == ["emit", "memory"], (
        f"answer must reach the client before the best-effort write: {order}"
    )
