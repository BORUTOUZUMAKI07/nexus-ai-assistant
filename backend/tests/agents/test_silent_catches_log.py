"""Best-effort failures are allowed, but they must never be silent."""
from __future__ import annotations

import pytest
from backend.app.agents.orchestrator import graph, nodes
from langchain_core.messages import AIMessage, HumanMessage


class _RecordingLogger:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict]] = []

    def warning(self, event: str, **kwargs) -> None:
        self.events.append((event, kwargs))

    def info(self, event: str, **kwargs) -> None:
        self.events.append((event, kwargs))


async def test_compaction_failure_is_logged(monkeypatch):
    from backend.app.infrastructure.ai import litellm_client

    logger = _RecordingLogger()

    async def _fail(**_kwargs):
        raise RuntimeError("summarizer down")

    monkeypatch.setattr(graph, "logger", logger)
    monkeypatch.setattr(litellm_client.ai_client, "completion", _fail)

    messages = [
        HumanMessage(content=f"q{i}") if i % 2 == 0 else AIMessage(content=f"a{i}")
        for i in range(30)
    ]
    result = await graph._maybe_compact(messages)

    assert result is None
    assert any(event == "context_compaction_failed" for event, _ in logger.events)


def test_budget_check_failure_is_logged(monkeypatch):
    logger = _RecordingLogger()

    def _fail(_tokens):
        raise RuntimeError("budget service down")

    monkeypatch.setattr(nodes, "logger", logger)
    monkeypatch.setattr(nodes, "budget_exhausted", _fail)

    assert nodes._budget_stop({}, 100) == ""
    assert any(event == "budget_check_failed" for event, _ in logger.events)
