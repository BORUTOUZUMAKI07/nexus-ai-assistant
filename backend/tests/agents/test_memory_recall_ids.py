"""The planner must keep the IDs of the memories it recalled.

``planner_node`` created an empty ``recalled_items`` list, built only the text
block, and then logged ``count=len(recalled_items)`` — always zero. The
synthesizer's background reinforcement reads ``recalled_memories`` from state,
so no recalled memory was ever reinforced. This pins the read-back seam.
"""
from __future__ import annotations

import pytest

MEMORY_BLOCK = (
    "### Relevant User Context (from previous conversations):\n"
    "- User prefers dark mode in all tools\n"
)
RECALL_ITEMS = [
    {"id": "mem0-1", "db_id": "11111111-1111-4111-8111-111111111111", "memory": "User prefers dark mode"},
    {"id": "mem0-2", "memory": "User works on a payments team"},
]


class _Msg:
    def __init__(self, content: str, msg_type: str = "human") -> None:
        self.content = content
        self.type = msg_type


@pytest.mark.asyncio
async def test_planner_returns_recalled_memory_ids(monkeypatch):
    """RED: the block and its item IDs must come from the same recall."""
    from backend.app.agents.orchestrator import nodes

    seen = {}

    async def _recall_with_ids(**kwargs):
        seen["kwargs"] = kwargs
        return MEMORY_BLOCK, RECALL_ITEMS

    async def _noop_completion(**_kwargs):
        return "DIRECT"

    monkeypatch.setattr(
        nodes.long_term_memory, "build_memory_context_block_with_ids", _recall_with_ids
    )
    monkeypatch.setattr(nodes.ai_client, "completion", _noop_completion)

    result = await nodes.planner_node(
        {
            "messages": [_Msg("What should I use?")],
            "user_id": "u1",
            "conversation_id": "c1",
        }
    )

    assert result["user_memories"] == [MEMORY_BLOCK]
    assert result["recalled_memories"] == RECALL_ITEMS
    assert seen["kwargs"]["query"] == "What should I use?"
    assert seen["kwargs"]["limit"] == 8


@pytest.mark.asyncio
async def test_planner_recall_failure_is_fail_open(monkeypatch):
    """A Mem0 outage must leave the plan path usable."""
    from backend.app.agents.orchestrator import nodes

    async def _recall_with_ids(**_kwargs):
        raise RuntimeError("mem0 down")

    monkeypatch.setattr(
        nodes.long_term_memory, "build_memory_context_block_with_ids", _recall_with_ids
    )

    result = await nodes.planner_node(
        {
            "messages": [_Msg("hi")],
            "user_id": "u1",
            "conversation_id": "c1",
        }
    )

    assert result["user_memories"] == []
    assert result["recalled_memories"] == []


@pytest.mark.asyncio
async def test_planner_honours_an_explicit_memory_opt_out(monkeypatch):
    """RED: disabled memory must skip the planner's mem0 recall entirely."""
    from backend.app.agents.orchestrator import nodes

    recalls = []

    async def _disabled(*_args, **_kwargs):
        return False

    async def _recall_with_ids(**kwargs):
        recalls.append(kwargs)
        return MEMORY_BLOCK, RECALL_ITEMS

    monkeypatch.setattr(nodes, "is_memory_enabled", _disabled, raising=False)
    monkeypatch.setattr(
        nodes.long_term_memory, "build_memory_context_block_with_ids", _recall_with_ids
    )

    result = await nodes.planner_node(
        {"messages": [_Msg("What should I use?")], "user_id": "u1", "conversation_id": "c1"}
    )

    assert recalls == []
    assert result["user_memories"] == []
    assert result["recalled_memories"] == []
    assert result["memory_enabled"] is False
