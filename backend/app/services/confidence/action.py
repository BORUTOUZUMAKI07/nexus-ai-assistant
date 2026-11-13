"""
Act on the calibrated confidence gate.

The defect this fixes: ``ConfidenceService.decide`` returns
``{"action": "answer" | "hedge", ...}``, the synthesizer logged it and returned
it in graph state, the stream route even publishes it as an SSE event -- and
nothing branched on it. A response the system itself judged to be
low-confidence was delivered to the user as a flat, unqualified claim.

That is the specific harm: a calibrated gate that does not gate. The
confidence number was decorative.

Design constraints, in priority order:

1. **Deterministic.** No LLM call. Deciding whether to hedge must be cheaper
   than the answer it qualifies, and a judge that calls a model to decide
   whether to trust a model is a second thing to be wrong.

2. **Never degrade a good answer.** Hedging a correct, well-supported response
   trains users to ignore the signal, which is worse than having no signal at
   all. The gate's own verdict is the only input, and the floor on how badly a
   response must score before it is touched is enforced here.

3. **Fail-open.** If the gate is unavailable, missing, or throws, the response
   ships unchanged. An unhedged answer is recoverable; a swallowed answer is
   not.

4. **One-shot, never iterative.** This is not a re-answer loop. The synthesizer
   already has a bounded critic revision loop; adding a second retry here would
   double cost on precisely the turns that can least afford it.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

#: Confidence below which a response is never touched, whatever the mode.
#: Guards against a miscalibrated gate turning every answer into a disclaimer.
NEVER_HEDGE_BELOW = 0.15

#: Answer-starts that already carry their own hedge. Re-prefixing one of these
#: produces "I'm not certain, but it might be the case that...", which reads as
#: a model malfunction rather than a calibrated answer.
#:
#: The first alternative is this module's own note. Idempotence matters: a
#: response that re-enters the gate (a critic revision, a resume after an
#: interrupt) must not collect a second disclaimer.
_ALREADY_HEDGED = re.compile(
    r"^\s*(?:\*low confidence|\*unverified|i(?:'m| am) not (?:certain|sure|able)|"
    r"i (?:don'?t|do not) know|it (?:depends|is unclear|is hard to say)|"
    r"there(?:'s| is) no (?:definite|clear)|unfortunately|regrett?fully|as an ai)\b",
    re.IGNORECASE,
)

#: Refusal bodies for the abstain mode. Kept plain and specific rather than
#: vague, so the user knows what to do next instead of just being blocked.
_ABSTAIN_BODY = (
    "I'm not confident enough in the sources I have to answer this "
    "reliably. Rather than guess, could you rephrase your question, or "
    "share a specific document or dataset to work from?"
)

#: Unqualified answer patterns we will not annotate, because the "answer" is
#: already a question or an offer rather than a factual claim.
_NOT_A_CLAIM = re.compile(
    r"^\s*(?:what|who|when|where|why|how|which|can|could|would|should|do|does|is|are)\b.*\?\s*$",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class HedgeResult:
    """Outcome of applying the gate: the text to send, and why."""

    text: str
    hedged: bool
    reason: str
    confidence: float
    action: str

    def __bool__(self) -> bool:
        return self.hedged


def should_hedge(decision: dict[str, Any] | None, mode: str = "annotate") -> tuple[bool, str]:
    """Decide whether a response must be qualified. Returns (verdict, reason).

    The reason string is returned rather than just logged because the caller
    streams it to the UI: a hedge the user can see the basis for is honest,
    and one that is silently inserted is not.
    """
    if mode in ("none", "", None):
        return False, "hedge_mode_none"
    if not decision:
        return False, "no_decision"

    confidence = decision.get("confidence")
    if confidence is None:
        return False, "decision_has_no_confidence"

    try:
        confidence = float(confidence)
    except (TypeError, ValueError):
        return False, "non_numeric_confidence"

    # The gate's own verdict is authoritative and is checked first. Note that
    # ConfidenceService scores ungrounded chat as 0.95 / "answer" on purpose
    # (see its own test suite), so ordinary chit-chat never reaches the
    # grounding branches below.
    if decision.get("action") != "hedge":
        return False, "gate_says_answer"

    if confidence < NEVER_HEDGE_BELOW:
        # The gate is broken or the response is noise; annotating noise makes
        # the signal look broken too. Ship it and let other layers catch it.
        return False, "below_hedge_floor"

    if not decision.get("grounded", False):
        # Low confidence with nothing to ground it in: the answer is
        # unsupported rather than weakly supported. That is the abstain case,
        # not the annotate case.
        return True, "low_confidence_ungrounded"

    return True, "below_confidence_threshold"


def apply_confidence_action(
    response_text: str,
    decision: dict[str, Any] | None,
    mode: str = "annotate",
) -> HedgeResult:
    """Apply the gate's verdict to a response.

    ``annotate``  - prefix an explicit uncertainty note, keeping the answer
                    readable. The default: the user usually still wants the
                    best available answer, clearly labelled.
    ``abstain``   - replace the answer entirely. Reserved for the case where
                    the answer is actively harmful if believed.
    """
    text = (response_text or "").strip()
    confidence = 0.0
    action = "answer"
    if isinstance(decision, dict):
        confidence = decision.get("confidence") or 0.0
        action = str(decision.get("action") or "answer")

    verdict, reason = should_hedge(decision, mode=mode)

    if not verdict:
        return HedgeResult(text, False, reason, confidence, action)

    # Never annotate something that is not a factual claim, or something that
    # already hedges: both produce a worse answer than leaving it alone.
    if _NOT_A_CLAIM.match(text):
        return HedgeResult(text, False, "not_a_factual_claim", confidence, action)
    if _ALREADY_HEDGED.match(text):
        return HedgeResult(text, False, "already_hedged", confidence, action)

    if mode == "abstain":
        return HedgeResult(_ABSTAIN_BODY, True, f"{reason}:abstain", confidence, action)

    note = _uncertainty_note(decision, confidence)
    return HedgeResult(f"{note}\n\n{text}", True, f"{reason}:annotate", confidence, action)


def _uncertainty_note(decision: dict[str, Any] | None, confidence: float) -> str:
    """Build the prefix. One sentence, stating the reason, no corporate hedging.

    Mentions sourcing when the gate flagged weak grounding, because "I am less
    certain" and "my sources did not support this" call for different reactions
    from a reader.
    """
    grounded = bool((decision or {}).get("grounded", False))
    pct = max(1, min(99, round(confidence * 100)))
    if grounded:
        return (
            f"*Low confidence ({pct}%) — the sources I found only partially "
            f"support this. Worth verifying before you rely on it.*"
        )
    return (
        f"*Unverified ({pct}%) — I could not confirm this against the "
        f"material available. Treat it as a starting point, not a fact.*"
    )
