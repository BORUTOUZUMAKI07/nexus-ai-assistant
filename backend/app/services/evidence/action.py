"""
Act on the evidence gate's verdict.

The defect this fixes: ``EvidenceGate.verify_evidence_support`` returns a
verdict, the synthesizer logged it, returned it in graph state and streamed it
to the UI as a "Low evidence" pill -- and delivered the answer unchanged. A
gate that only produces a badge is decorative: an answer the system itself
judged unsupported reached the user as a flat, verified claim.

Design constraints (same as the sibling ``services/confidence/action.py``):

1. **Deterministic.** No model call. Deciding whether an answer needs a
   grounding caveat must be cheaper than producing the answer.
2. **Never degrade a grounded answer.** Only a verdict the gate actually
   evaluated and failed qualifies the text. `None` means "not evaluated",
   a missing/malformed verdict means "gate broken" -- neither is a failure.
3. **Fail-open.** Any caller exception leaves the answer untouched.
4. **One note, never two.** The confidence gate may already have prefixed an
   uncertainty note; stacking a second caveat reads as a malfunction.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

#: Answers that already carry their own caveat, including the note produced by
#: ``services.confidence.action``. Re-qualifying one of these makes the response
#: look broken rather than calibrated, and idempotence matters on a resumed or
#: re-entered turn.
_ALREADY_QUALIFIED = re.compile(
    r"^\s*(?:\*low (?:evidence|confidence)|\*unverified|i(?:'m| am) not "
    r"(?:certain|sure|able)|i (?:don'?t|do not) know|it (?:depends|is "
    r"unclear|is hard to say)|there(?:'s| is) no (?:definite|clear)|"
    r"unfortunately|regrett?fully|as an ai)\b",
    re.IGNORECASE,
)

_NOT_A_CLAIM = re.compile(
    r"^\s*(?:what|who|when|where|why|how|which|can|could|would|should|do|does|"
    r"did|will|is|are)\b.*\?\s*$",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class EvidenceActionResult:
    """Outcome of applying the gate: the text to send, and why."""

    text: str
    acted: bool
    reason: str
    confidence: float

    def __bool__(self) -> bool:
        return self.acted


def apply_evidence_action(
    response_text: str, verdict: dict[str, Any] | None
) -> EvidenceActionResult:
    """Qualify an answer the evidence gate actually found unsupported.

    ``passed_gate is None`` means the gate was never exercised (no sources were
    retrieved) and is deliberately not a failure. An absent or malformed result
    means the gate itself is broken, which is also not permission to change the
    answer. Only a genuine, evaluated failure takes action.
    """
    text = (response_text or "").strip()
    if not isinstance(verdict, dict):
        return EvidenceActionResult(text, False, "no_verdict", 0.0)

    passed = verdict.get("passed_gate")
    if passed is None:
        return EvidenceActionResult(text, False, "gate_not_evaluated", 0.0)
    if passed is not True and passed is not False:
        return EvidenceActionResult(text, False, "invalid_verdict", 0.0)
    if passed is True:
        return EvidenceActionResult(text, False, "gate_passed", _score(verdict, 0.0))

    confidence = _score(verdict, None)
    if confidence is None:
        return EvidenceActionResult(text, False, "missing_confidence", 0.0)
    if _NOT_A_CLAIM.match(text):
        return EvidenceActionResult(text, False, "not_a_factual_claim", confidence)
    if _ALREADY_QUALIFIED.match(text):
        return EvidenceActionResult(text, False, "already_qualified", confidence)

    pct = max(1, min(99, round(confidence * 100)))
    note = (
        f"*Low evidence ({pct}%) — the sources retrieved for this question did "
        "not support these claims. Verify before relying on it.*"
    )
    return EvidenceActionResult(
        f"{note}\n\n{text}", True, "below_evidence_threshold", confidence
    )


def _score(verdict: dict[str, Any], default: float | None) -> float | None:
    try:
        return float(verdict.get("confidence_score"))
    except (TypeError, ValueError):
        return default
