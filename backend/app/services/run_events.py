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

__all__ = ["finished_run_events", "sse_frame"]


def sse_frame(payload: dict[str, Any]) -> str:
    """One SSE frame. The ``data: `` prefix and blank-line terminator are the
    wire format the SSE spec requires, and every frame in this module goes
    through here so a typo cannot produce a frame the client silently skips."""
    return f"data: {json.dumps(payload)}\n\n"


def finished_run_events(values: Any) -> list[str]:
    """SSE frames describing a run that has finished.

    Returns an empty list for anything that is not a mapping, so a checkpoint
    that is missing or has been serialized oddly yields silence rather than an
    exception mid-stream -- the answer has already been delivered at this
    point, and failing here would replace a good reply with a stream error.
    """
    if not isinstance(values, dict):
        return []

    frames: list[str] = []

    critique = values.get("critique")
    if critique:
        frames.append(
            sse_frame(
                {
                    "type": "critique",
                    "critique": critique,
                    "revision_count": values.get("revision_count", 0),
                }
            )
        )

    frames.append(
        sse_frame(
            {
                "type": "quality",
                "evidence_score": values.get("evidence_score", 0.0),
                "evidence_gate_passed": values.get("evidence_gate_passed", None),
            }
        )
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
            sse_frame(
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
        )

    return frames
