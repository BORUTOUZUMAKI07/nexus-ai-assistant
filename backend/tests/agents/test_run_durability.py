"""Checkpoint durability on the one graph execution path this app has.

LangGraph 1.2 defaults ``astream``/``astream_events`` to ``durability="async"``:
the checkpoint write is started but not awaited before the next step runs. That
is fine for a fire-and-forget script and wrong for this app, which reads the
checkpoint back from a *different request* (HITL resume calls ``aget_state`` to
find the parked interrupt, and a rejoin reads the run that produced it). Official
docs say ``sync`` is the mode that commits every checkpoint before the next step
starts, and is required for crash-safe idempotency.

These tests check both halves: the production call actually asks for ``sync``,
and the custom-stream delivery this executor depends on still works with it.
"""
from __future__ import annotations

from backend.app.services.runs.executor import iter_graph_frames
from langgraph.checkpoint.memory import MemorySaver
from langgraph.config import get_stream_writer
from langgraph.graph import END, START, StateGraph
from typing_extensions import TypedDict

CONFIG = {"configurable": {"thread_id": "durability"}}
DELTA = {"type": "text_delta", "content": "sync-safe"}


class _State(TypedDict, total=False):
    marker: str


class _RecordingGraph:
    """Captures the call the executor makes, without running a graph."""

    def __init__(self) -> None:
        self.kwargs: dict = {}
        self.input = None
        self.config = None

    async def astream_events(self, input, config=None, **kwargs):
        self.input = input
        self.config = config
        self.kwargs = kwargs
        for event in ():  # no events: this fake only records the call
            yield event


async def test_iter_graph_frames_asks_for_synchronous_checkpoints():
    # The default is "async": a checkpoint can still be in flight when the next
    # node starts, and a process dying there leaves HITL resume with no parked
    # interrupt to read back. "sync" is the documented mode that commits each
    # checkpoint before continuing.
    graph = _RecordingGraph()

    frames = [
        frame
        async for frame, _ in iter_graph_frames(
            graph, config=CONFIG, graph_input={"marker": "x"}
        )
    ]

    assert frames == []
    assert graph.kwargs.get("durability") == "sync"
    # Durability must not disturb the two measured stream settings.
    assert graph.kwargs.get("version") == "v2"
    assert graph.kwargs.get("stream_mode") == "custom"


async def test_sync_durability_keeps_custom_writer_frames_deliverable():
    # If requesting sync durability silently dropped the custom stream, exec
    # mode would lose its answer entirely: its text arrives through the writer,
    # not through a state delta. Run the real thing, not a fake that assumes the
    # combination works.
    async def writer_node(state: _State) -> _State:
        get_stream_writer()(DELTA)
        return state

    workflow = StateGraph(_State)
    workflow.add_node("writer", writer_node)
    workflow.add_edge(START, "writer")
    workflow.add_edge("writer", END)
    graph = workflow.compile(checkpointer=MemorySaver())

    frames = [
        frame
        async for frame, _ in iter_graph_frames(
            graph, config=CONFIG, graph_input={"marker": "x"}
        )
    ]

    assert DELTA in frames
