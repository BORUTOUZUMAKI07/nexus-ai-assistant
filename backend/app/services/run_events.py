"""The SSE frames that tell the client what became true of a finished run.

Extracted from the stream route for two reasons, and the second is the
important one.

The *first* is that ``api/v1/`` is meant to hold routers only, and a run's
result-summary is not routing. The *second* is testability: those three frames
were three hand-rolled ``yield json.dumps(...)`` statements buried in the
middle of a 300-line async generator with live database and graph dependencies,
which is exactly the shape of code that cannot be unit tested -- and untestable
code is code whose behaviour is asserted by nobody.

That is not hypothetical. When the artifact frame was first added, a revert
harness proved that deleting its emission entirely changed no test's result,
because no test could reach it. The frame is a user's only notification that a
document was saved for them; a change that silently stopped emitting it would
have shipped looking green. Now the decision that emits it is a pure function
that is tested directly.

Order matters and is part of the contract: the client must have the final
assistant text before it is asked to open a canvas over it, so ``artifact``
comes after ``quality``.
"""

from __future__ import annotations

import json
from typing import Any

__all__ = ["finished_run_events", "finished_run_payloads", "sse_frame"]


def sse_frame(payload: dict[str, Any]) -> str:
    """One SSE frame. The ``data: `` prefix and blank-line terminator are the
    wire format the SSE spec requires, and every frame in this module goes
    through here so a typo cannot produce a frame the client silently skips."""
    return f"data: {json.dumps(payload)}\n\n"


def finished_run_payloads(values: Any) -> list[dict[str, Any]]:
    """The frames describing a run that has finished, as *objects*.

    Returns an empty list for anything that is not a mapping, so a checkpoint
    that is missing or has been serialized oddly yields silence rather than an
    exception mid-stream -- the answer has already been delivered at this
    point, and failing here would replace a good reply with a stream error.

    ## Why the payloads are separate from the wire format

    ``finished_run_events`` returns ready-to-yield SSE *strings*, because for its
    original only caller that was exactly right. The run log, however, stores
    frames as JSON objects, and a string spliced into that list is stored as a
    JSON string and then serialised a second time on the way out:

        data: "data: {\\"type\\": \\"quality\\"}\\n\\n"

    The client's translator parses that and finds no ``type``, so the frame is
    dropped -- a critique, a quality score and the notification that a document
    was saved all silently lost, with no error anywhere and every test green,
    because each layer's own tests pass in isolation. See AGENTS.md §2: an event
    nobody can parse is indistinguishable from one that was never emitted.

    So the object list is the reusable thing and the SSE string is derived from
    it at the edge. ``finished_run_events`` keeps its exact previous behaviour,
    and its tests are unchanged -- one definition, two renderings.
    """
    if not isinstance(values, dict):
        return []

    frames: list[dict[str, Any]] = []

    critique = values.get("critique")
    if critique:
        frames.append(
            {
                "type": "critique",
                "critique": critique,
                "revision_count": values.get("revision_count", 0),
            }
        )

    frames.append(
        {
            "type": "quality",
            "evidence_score": values.get("evidence_score", 0.0),
            "evidence_gate_passed": values.get("evidence_gate_passed", None),
        }
    )

    # A durable document was saved (or re-versioned) for this turn.
    #
    # Guarded rather than always sent: almost every turn produces no artifact,
    # and an event the client must special-case on every single turn is a
    # standing protocol obligation for no information.
    #
    # Only the id crosses the wire. The canvas fetches the content itself, so a
    # 120k-character document is never duplicated into an event frame the client
    # then holds in memory a second time.
    artifact_id = values.get("artifact_id")
    if artifact_id:
        frames.append(
            {
                "type": "artifact",
                "artifact_id": artifact_id,
                "title": values.get("artifact_title", ""),
                "version": values.get("artifact_version", 1),
                # Absent means "created"; a regeneration explicitly sends
                # False, so the default must not collapse the two.
                "created": bool(values.get("artifact_created", True)),
            }
        )

    return frames


def finished_run_events(values: Any) -> list[str]:
    """``finished_run_payloads`` rendered as SSE frames.

    Order matters and is part of the contract: the client must have the final
    assistant text before it is asked to open a canvas over it, so ``artifact``
    comes after ``quality``.
    """
    return [sse_frame(payload) for payload in finished_run_payloads(values)]
