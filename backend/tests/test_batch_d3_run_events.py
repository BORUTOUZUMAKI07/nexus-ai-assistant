"""The SSE frames a finished run tells the client about.

These frames exist in `api/v1/conversations.py` as three inline ``yield
json.dumps(...)`` statements buried in the middle of a 300-line async generator
with live database and graph dependencies. That shape is untestable, and
untestable here was not theoretical: when the artifact frame was first written,
a revert harness deleted its emission outright and **no test changed result** --
the row still went to the database, the API still worked, the canvas still had
the document, and the user was simply never told.

They now come from `services/run_events.py`, a pure function. This file is what
makes deleting the artifact frame fail.
"""

import json

from backend.app.services.run_events import finished_run_events, sse_frame

DOCUMENT_RUN = {
    "critique": "a bit thin",
    "revision_count": 1,
    "evidence_score": 0.82,
    "evidence_gate_passed": True,
    "artifact_id": "art-9",
    "artifact_title": "Q3 Report",
    "artifact_version": 1,
    "artifact_created": True,
}


def payloads(frames: list[str]) -> list[dict]:
    """Decode a frame list, asserting the SSE wire format on the way.

    A frame missing the ``data: `` prefix or the blank-line terminator is
    silently skipped by every browser EventSource parser, so the format itself
    is part of what these tests assert.
    """
    decoded = []
    for frame in frames:
        assert frame.startswith("data: "), f"bad prefix: {frame!r}"
        assert frame.endswith("\n\n"), f"missing terminator: {frame!r}"
        decoded.append(json.loads(frame[len("data: ") : -2]))
    return decoded


# ─── the artifact frame ──────────────────────────────────────────────────────


def test_a_document_turn_emits_the_artifact_frame():
    frames = finished_run_events(DOCUMENT_RUN)
    artifact = [p for p in payloads(frames) if p["type"] == "artifact"]
    assert len(artifact) == 1, f"expected exactly one artifact frame, got {frames}"
    assert artifact[0] == {
        "type": "artifact",
        "artifact_id": "art-9",
        "title": "Q3 Report",
        "version": 1,
        "created": True,
    }


def test_a_chat_turn_emits_no_artifact_frame():
    """The overwhelmingly common case, and the one that must stay quiet.

    A frame emitted on every turn would be a standing protocol obligation: the
    client would have to special-case it forever for no information.
    """
    frames = finished_run_events({"evidence_score": 0.4})
    assert [p for p in payloads(frames) if p["type"] == "artifact"] == []


def test_a_regeneration_is_reported_as_not_created():
    """The canvas badges "new" versus "version N"; collapsing the two lies."""
    frames = finished_run_events({**DOCUMENT_RUN, "artifact_created": False})
    artifact = [p for p in payloads(frames) if p["type"] == "artifact"][0]
    assert artifact["created"] is False
    assert artifact["version"] == 1


def test_a_missing_created_flag_defaults_to_created():
    """Absent means new. Defaulting to False would mislabel every first save."""
    frames = finished_run_events(
        {"artifact_id": "art-1", "artifact_title": "T", "artifact_version": 1}
    )
    artifact = [p for p in payloads(frames) if p["type"] == "artifact"][0]
    assert artifact["created"] is True


def test_an_empty_artifact_id_is_treated_as_absent():
    """An empty id is not a usable handle; emitting it would open nothing."""
    frames = finished_run_events({"artifact_id": "", "artifact_title": "T"})
    assert [p for p in payloads(frames) if p["type"] == "artifact"] == []


def test_the_frame_carries_the_id_and_never_the_body():
    """A 120k-char document in an event frame would be held in memory twice.

    The canvas fetches the content itself; if `content` ever appears here, this
    fails.
    """
    frames = finished_run_events({**DOCUMENT_RUN, "content": "SECRET BODY"})
    assert "SECRET BODY" not in "".join(frames)


# ─── ordering: the contract with the client ──────────────────────────────────


def test_the_artifact_frame_comes_after_quality():
    """The client must have the final text before opening a canvas over it."""
    order = [p["type"] for p in payloads(finished_run_events(DOCUMENT_RUN))]
    assert order.index("quality") < order.index("artifact")


def test_critique_leads_and_is_omitted_when_absent():
    with_critique = [p["type"] for p in payloads(finished_run_events(DOCUMENT_RUN))]
    assert with_critique[0] == "critique"

    without = [p["type"] for p in payloads(finished_run_events({"evidence_score": 1.0}))]
    assert "critique" not in without


def test_quality_is_always_emitted():
    """Unlike critique, this one is unconditional -- it is the evidence score."""
    for values in ({}, {"artifact_id": "a"}, {"critique": "x"}):
        types = [p["type"] for p in payloads(finished_run_events(values))]
        assert "quality" in types, f"quality missing for {values!r}"


def test_quality_defaults_are_safe_not_absent():
    """A client reading `undefined` is worse than one reading a real zero."""
    quality = [p for p in payloads(finished_run_events({})) if p["type"] == "quality"][0]
    assert quality["evidence_score"] == 0.0
    assert quality["evidence_gate_passed"] is None


# ─── degenerate input: the answer is already delivered ───────────────────────


def test_a_non_mapping_snapshot_yields_nothing():
    """At this point the reply has already been sent.

    Raising here would replace a good answer with a stream error, which is a
    strictly worse outcome than the client not hearing about the quality score.
    """
    for values in (None, [], "checkpoint", 42, object()):
        assert finished_run_events(values) == []


def test_an_empty_snapshot_still_reports_quality():
    """Not the same as no snapshot: an empty dict is a real, if bare, state."""
    frames = finished_run_events({})
    assert [p["type"] for p in payloads(frames)] == ["quality"]


# ─── the frame format itself ─────────────────────────────────────────────────


def test_frames_are_json_encoded_not_string_interpolated():
    """A title containing a quote must not be able to break the frame."""
    nasty = 'He said "hi"\nand left\\'
    frames = finished_run_events(
        {"artifact_id": "a", "artifact_title": nasty, "artifact_version": 1}
    )
    artifact = [p for p in payloads(frames) if p["type"] == "artifact"][0]
    assert artifact["title"] == nasty


def test_a_newline_in_a_title_cannot_inject_a_second_frame():
    """The one genuinely dangerous direction for a hand-built SSE frame.

    A raw newline in a value would let a value terminate its own frame and
    start another -- so a model-supplied title could smuggle in an event type of
    its choosing. json.dumps is what prevents that; this pins it.
    """
    frames = finished_run_events(
        {
            "artifact_id": "a",
            "artifact_title": "x\n\ndata: {\"type\":\"injected\"}\n\n",
            "artifact_version": 1,
        }
    )
    assert len(frames) == 2, "the title smuggled in an extra frame"
    types = [p["type"] for p in payloads(frames)]
    assert types.count("artifact") == 1
    assert "injected" not in types


def test_sse_frame_shape():
    frame = sse_frame({"type": "x"})
    assert frame.startswith("data: ")
    assert frame.endswith("\n\n")
    assert json.loads(frame[6:-2]) == {"type": "x"}
