"""The run must survive the synthesizer's own token stream.

## The defect this covers

``iter_graph_frames`` deliberately ignores the synthesizer's raw draft — it
arrives before PII redaction and before the confidence hedge, and the sanitised
text comes through the writer's ``text_delta`` drip instead. That skip was
written as a bare ``return`` inside the generator's event loop, which does not
skip a chunk: it ends the generator.

Consequences, all of them invisible from the client and from the backend's own
logs:

* the answer's ``text_delta`` frames were never yielded after the raw draft, so
  ``emitted_text`` in ``execute_run`` stayed empty;
* ``_persist_reply`` returns early on empty text, so neither the assistant
  message nor the usage row was written — the transcript reloaded empty and
  usage stayed at zero;
* the run still reported ``done`` to the client, so nothing anywhere errored.

The test drives the generator directly with the two events that matter, in the
order a real run produces them: a framing event, the synthesizer's raw token
stream, then the writer's sanitised frame. It asserts on the frame that comes
*after* the raw stream, which is exactly what the ``return`` swallowed.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from backend.app.services.runs.executor import iter_graph_frames  # noqa: E402


class _FakeGraph:
    """Replays a fixed event list, in order, once."""

    def __init__(self, events: list[dict[str, Any]]) -> None:
        self._events = events

    async def astream_events(self, **_kwargs: Any):
        for event in self._events:
            yield event


def _collect(events: list[dict[str, Any]]) -> list[tuple[dict[str, Any], bool]]:
    graph = _FakeGraph(events)

    async def run() -> list[tuple[dict[str, Any], bool]]:
        return [
            pair
            async for pair in iter_graph_frames(
                graph,
                config={"configurable": {"thread_id": "thread-1"}},
                graph_input={"messages": []},
            )
        ]

    return asyncio.run(run())


def test_raw_synthesizer_stream_does_not_end_the_frame_stream() -> None:
    events = [
        # The synthesizer streaming its draft token-by-token. Its content must
        # not reach the client, but it must not stop the generator either.
        {
            "event": "on_chat_model_stream",
            "name": "synthesizer",
            "metadata": {"langgraph_node": "synthesizer"},
            "data": {"chunk": SimpleNamespace(content="RAW UNSANITISED DRAFT")},
        },
        # The writer's sanitised frame, delivered on the root run as a custom
        # payload. This is the answer the user sees and the text that is
        # persisted, so it has to arrive after the raw draft was skipped.
        {
            "event": "on_chain_stream",
            "name": "LangGraph",
            "data": {"chunk": {"type": "text_delta", "content": "Final answer."}},
        },
        # A later event, to prove the loop kept iterating rather than ending on
        # the last frame available at that moment. A tool start is used rather
        # than a node start because it yields a frame unconditionally, while a
        # node start only does so for nodes in the status-label table.
        {
            "event": "on_tool_start",
            "name": "web_search",
            "run_id": "tool-run-1",
            "data": {"input": {"query": "nexus"}},
        },
    ]

    frames = _collect(events)
    payloads = [frame for frame, _is_text in frames]
    text = "".join(
        str(frame.get("content", ""))
        for frame, is_text in frames
        if is_text
    )

    assert "Final answer." in text, f"sanitised frame lost: {payloads}"
    assert "RAW UNSANITISED DRAFT" not in text, "raw draft must never be forwarded"
    assert any(frame.get("type") == "tool_call" for frame in payloads), (
        "the generator stopped before later events were yielded"
    )
