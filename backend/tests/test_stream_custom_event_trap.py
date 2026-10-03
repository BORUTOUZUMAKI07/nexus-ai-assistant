"""How custom-stream writes actually surface through `astream_events`.

Why this file exists
--------------------
`api/v1/conversations.py` streams the graph with `astream_events(version="v2")`.
Somebody adding a mid-run event will reach for `get_stream_writer()`, pass
`stream_mode="custom"` because the tutorials say to, and write a handler for
`on_custom_event`. Every one of those three assumptions needs checking, and two
of them are wrong in ways that produce no error.

The measured matrix (langgraph 1.2.11)
--------------------------------------
One variable at a time, in a fresh process, asserted below rather than quoted:

    astream_events(v2)                          -> 0 payloads
    astream_events(v2, stream_mode="custom")     -> payload, as an `on_chain_stream`
                                                    on the ROOT `LangGraph` run,
                                                    data["chunk"] == payload
    astream_events(v2, stream_mode=["custom"])   -> 0 payloads        <-- the trap
    astream(stream_mode="custom")                -> payload, unwrapped
    astream_events(v2, stream_mode="bogus_mode") -> no raise, and the root
                                                    run's on_chain_stream is
                                                    suppressed entirely

The list form is what anyone writes when subscribing to more than one mode, and
it delivers nothing. Neither does an outright typo -- and a typo is *worse*,
because it silences the root deltas too. Both look identical to a working
subscription from the call site.

`on_custom_event` is never emitted. The string occurs once in the whole
package, at `langgraph/pregel/_retry.py:312`, as an idle-timer touch handler on
an internal scope class.

Correction history, kept because it is the point
-----------------------------------------------
This file's first version asserted the OPPOSITE of the truth: that custom writes
never reach `astream_events` even with the kwarg, and that `on_custom_event`
appears zero times. Both were wrong, and the reason they were wrong is worth
recording -- a probe printed six event *names* for the no-kwarg case and full
event *data* for the kwarg case, and the two were read as one result. Counting
events is not measuring content. The repo's own rule applies (AGENTS.md 9.14):
assert each claim on its own axis, or a green test proves nothing.

Run: `cd backend && uv run pytest tests/test_stream_custom_event_trap.py`
"""
from __future__ import annotations

from pathlib import Path

import langgraph
from langgraph.config import get_stream_writer
from langgraph.graph import END, START, StateGraph
from typing_extensions import TypedDict

CUSTOM = {"type": "custom", "v": 1}


class _State(TypedDict, total=False):
    """Minimal state. Deliberately not the app's AgentState -- this is a
    property of the stream API, not of our schema, and importing the real one
    would drag the settings/database chain into a stream test."""

    marker: str


async def _writer_node(state: _State) -> _State:
    get_stream_writer()(CUSTOM)
    return state


def _graph():
    workflow = StateGraph(_State)
    workflow.add_node("n", _writer_node)
    workflow.add_edge(START, "n")
    workflow.add_edge("n", END)
    return workflow.compile()


async def _events(**kwargs) -> list[dict]:
    """Run the graph and collect the raw v2 event dicts.

    A fresh graph per call, so no test can pass because of what a previous one
    left behind in a module-level checkpointer or contextvar.
    """
    out: list[dict] = []
    async for event in _graph().astream_events(
        {"marker": "x"}, version="v2", **kwargs
    ):
        out.append(event)
    return out


def _custom_chunks(events: list[dict]) -> list[dict]:
    """Custom payloads as they actually arrive: a root-run `on_chain_stream`
    whose `data["chunk"]` is exactly what the writer was handed."""
    return [
        e["data"]["chunk"]
        for e in events
        if e.get("event") == "on_chain_stream"
        and e.get("name") == "LangGraph"
        and isinstance(e.get("data"), dict)
        and e["data"].get("chunk") == CUSTOM
    ]


def _root_state_deltas(events: list[dict]) -> list[dict]:
    """Root-run state deltas -- the thing a custom chunk is mistaken for."""
    return [
        e["data"]["chunk"]
        for e in events
        if e.get("event") == "on_chain_stream"
        and e.get("name") == "LangGraph"
        and isinstance(e.get("data"), dict)
        and e["data"].get("chunk") != CUSTOM
    ]


async def test_custom_stream_is_not_delivered_unless_asked_for():
    """The default. This is why adding a mid-run event without the kwarg
    produces silence rather than an error."""
    events = await _events()
    assert _custom_chunks(events) == []
    # Prove the node really did write, so this is "not delivered" and not
    # "never written". Without this the test would pass just as happily
    # against a graph whose writer is a no-op.
    assert _root_state_deltas(events), "the graph produced no root delta at all"


async def test_string_form_delivers_it_on_the_root_run():
    """The positive case. Everything else in this file is measured against it."""
    assert _custom_chunks(await _events(stream_mode="custom")) == [CUSTOM]


async def test_list_form_delivers_nothing():
    """The nastiest item in the table.

    `stream_mode=["custom"]` is the natural thing to write when you want custom
    events *plus* another mode, and it silently subscribes to nothing. Same
    visible result as forgetting the kwarg, so the two mistakes are
    indistinguishable from the outside.
    """
    assert _custom_chunks(await _events(stream_mode=["custom"])) == []


async def test_a_bogus_stream_mode_does_not_raise():
    """Why none of the above is self-announcing.

    A typo is accepted -- and worse, it does not merely fail to deliver the
    custom payload, it suppresses the root run's `on_chain_stream` entirely. So
    an invalid mode makes the graph look like it is producing *less*, with no
    error anywhere. Anyone who fat-fingers the string loses the root deltas and
    has no signal that they did.
    """
    events = await _events(stream_mode="bogus_mode")

    assert _custom_chunks(events) == []
    assert _root_state_deltas(events) == [], (
        "a bogus stream_mode no longer suppresses root on_chain_stream; the "
        "comment at conversations.py::astream_events needs rewording."
    )

    # The graph still ran. This is not an error path and not a no-op -- it is a
    # narrower stream than asked for, which is why it reads as working.
    assert any(e.get("event") == "on_chain_end" for e in events)
    assert any(e.get("name") == "n" for e in events)


async def test_the_default_stream_carries_root_deltas_the_bogus_one_drops():
    """The contrast that makes the previous test mean something: with no kwarg
    the same graph does emit a root delta. Without this pair, 'no root deltas'
    could just be a property of this graph rather than of the bogus mode."""
    assert _root_state_deltas(await _events())


async def test_the_payload_is_not_its_own_event_type():
    """`kind == "on_custom_event"` never matches, so that handler is dead code
    that reads correct."""
    events = await _events(stream_mode="custom")
    kinds = {e.get("event") for e in events}
    assert "on_custom_event" not in kinds
    # And it is not smuggled under another name either: the payload only ever
    # appears as `data["chunk"]` on an `on_chain_stream`.
    for event in events:
        data = event.get("data")
        if isinstance(data, dict) and data.get("chunk") == CUSTOM:
            assert event.get("event") == "on_chain_stream"
            assert event.get("name") == "LangGraph"


async def test_a_custom_chunk_lands_in_the_branch_that_already_handles_it():
    """The live risk, stated as the collision it actually is.

    `conversations.py` handles `on_chain_stream` by reading
    `data["chunk"]["messages"]`. With the kwarg present, a custom chunk arrives
    at exactly that branch. The payload has no `messages` key, so it no-ops --
    correct by accident. Pinning the collision means that if the chunk shape ever
    changes we find out here, rather than a user seeing `{'type': 'custom'}`.
    """
    chunks = _custom_chunks(await _events(stream_mode="custom"))
    # Non-vacuous: the loop below must actually execute.
    assert len(chunks) == 1, f"expected one custom chunk, got {chunks!r}"
    for chunk in chunks:
        # Mirrors the production branch exactly.
        msgs = chunk.get("messages") if isinstance(chunk, dict) else None
        assert not msgs, (
            "A custom writer payload now carries a 'messages' key, so the "
            "on_chain_stream branch in conversations.py would stream it to the "
            "user as assistant text."
        )
        assert not isinstance(chunk.get("content"), str)


async def test_astream_custom_yields_the_payload_unwrapped():
    """The shape people expect, and the reason the tutorials read as though
    `astream_events` should behave identically. It does not."""
    seen = []
    async for chunk in _graph().astream({"marker": "x"}, stream_mode="custom"):
        seen.append(chunk)
    assert CUSTOM in seen, f"astream(stream_mode='custom') yielded {seen!r}"


def test_on_custom_event_is_only_an_internal_touch_handler():
    """It exists in the package, but never as an emitted event.

    Pinned by *form*, not by absence: asserting the string vanishes from the
    source would fail on an unrelated refactor of _retry.py without meaning
    anything changed. What matters is that every occurrence is a handler
    definition, never a dispatch that puts the name into an event dict.
    """
    root = Path(next(iter(langgraph.__path__)))
    occurrences = [
        (p, line.strip())
        for p in root.rglob("*.py")
        for line in p.read_text(encoding="utf-8", errors="replace").splitlines()
        if "on_custom_event" in line
    ]
    for path, line in occurrences:
        assert "= _touch" in line or "def on_custom_event" in line, (
            f"on_custom_event appears in a form this test does not understand: "
            f"{path.name}: {line}. Re-check whether it is now emitted."
        )


def test_our_finished_run_frames_never_used_a_custom_stream():
    """Why none of the above has bitten us yet: critique / quality / artifact
    are read post-run via `aget_state`, not streamed mid-run."""
    from backend.app.services.run_events import finished_run_events

    frames = finished_run_events(
        {"critique": "too thin", "revision_count": 2, "evidence_score": 0.4}
    )
    assert any('"type": "quality"' in f for f in frames)
    assert any('"type": "critique"' in f for f in frames)

    # Only `quality` is unconditional. Critique and artifact stay conditional:
    # almost no turn produces a document, and an event the client must
    # special-case every turn is a standing obligation for no information.
    bare = finished_run_events({})
    assert len(bare) == 1
    assert '"type": "quality"' in bare[0]
    assert not any('"type": "artifact"' in f for f in bare)
    assert not any('"type": "critique"' in f for f in bare)
