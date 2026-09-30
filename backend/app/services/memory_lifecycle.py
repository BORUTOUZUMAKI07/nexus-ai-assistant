"""
Memory lifecycle policy for long-term user memory.

Kept deliberately free of I/O so the policy is unit-testable and so the hot
chat path never pays an LLM call to decide whether to write a memory.

Three problems this solves, all verified against the previous behaviour:

1. ``nodes.py`` wrote *every* assistant response to mem0. mem0 runs an LLM
   extraction pass per write, so "hi" and "thanks" cost a model call and
   leave a low-value memory behind. :func:`should_persist_memory` gates the
   write on stated intent or a restatement of something already known.

2. ``UserMemory.confidence`` defaulted to ``1.0`` and was never written
   again, so every memory was permanently maximally trusted. Memories that
   were never confirmed and never recalled should fade; memories that keep
   proving useful should be reinforced. :func:`apply_reinforcement` and
   :func:`decayed_confidence` implement that.

3. mem0 and the ``user_memories`` table were two independent stores of the
   same facts that never exchanged anything. :func:`normalize_for_dedupe`
   and :func:`is_duplicate` are the reconciliation primitive: the table is
   the durable, user-visible record, mem0 is the semantic index over it.
"""
from __future__ import annotations

import math
import re
import sys
from datetime import UTC, datetime, timedelta

# ── Write gating ────────────────────────────────────────────────────────────
#
# Deliberately regex-based, not model-based. Deciding whether to spend an LLM
# call on "should I remember this?" by calling an LLM is circular, and the
# project's free-tier-first rule rules it out anyway. These patterns are the
# high-signal phrasings of a durable fact, preference or instruction.

#: First-person statements of a durable attribute.
_INTENT_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p, re.IGNORECASE)
    for p in (
        r"\bi (?:prefer|like|hate|dislike|always|never|usually)\b",
        r"\bmy name is\b",
        r"\bcall me\b",
        r"\bi(?:'m| am) (?:a|an|the|working|building|using|based)\b",
        r"\bi work (?:at|on|with|for)\b",
        r"\bi (?:use|run|deploy|develop|write)\b",
        r"\bwe (?:use|run|deploy|use)\b",
        r"\bour (?:stack|policy|process|workflow|setup)\b",
        r"\bi(?:'d| would) prefer\b",
        r"\bdon'?t (?:ever )?(?:use|do|call|include)\b",
        r"\bremember (?:that|this|my)\b",
        r"\bfor future reference\b",
        r"\balways (?:use|format|respond|reply)\b",
        r"\bfrom now on\b",
    )
)

#: Corrections and contradictions are the highest-value memory signal there
#: is: the user is fixing a fact the assistant got wrong.
_CORRECTION_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p, re.IGNORECASE)
    for p in (
        r"\bno[,!.]? (?:it'?s|that'?s|you)\b",
        r"\bthat'?s (?:wrong|incorrect|not right)\b",
        r"\bi (?:already )?(?:said|told you|mentioned)\b",
        r"\bactually[,]?\b",
        r"\bi meant\b",
        r"\bnot quite\b",
    )
)

#: Conversational filler that must never become a memory. Checked first so a
#: message like "no thanks, that's fine" is rejected as chatter rather than
#: matched by the "don't" rule in _INTENT_PATTERNS.
_CHATTER_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p, re.IGNORECASE)
    for p in (
        r"^\s*(?:hi|hey|hello|yo|thanks|thank you|ty|thx|ok|okay|k|cool|nice|"
        r"great|awesome|perfect|sure|yes|yep|no|nope|yeah|got it|understood|"
        r"np|cheers|bye|goodbye|see you)\b[\s!.?]*$",
        r"^\s*(?:what|who|when|where|why|how)\b.*\?\s*$",  # a bare question
    )
)

#: Below this, a message is too short to carry durable information.
_MIN_INTENT_CHARS = 12


class MemoryWriteDecision:
    """Outcome of the write gate: the bool, the reason, and for logs."""

    __slots__ = ("should_write", "reason")

    def __init__(self, should_write: bool, reason: str) -> None:
        self.should_write = should_write
        self.reason = reason

    def __bool__(self) -> bool:
        return self.should_write

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"MemoryWriteDecision({self.should_write!r}, {self.reason!r})"


def should_persist_memory(
    user_text: str,
    assistant_text: str = "",
    repeated: bool = False,
) -> MemoryWriteDecision:
    """Decide whether this exchange is worth an LLM extraction call.

    Args:
        user_text: The user's latest message.
        assistant_text: The assistant's reply, used only to reject exchanges
            that produced no real content.
        repeated: True when this turn's content substantially repeats
            something already persisted. mem0 would deduplicate it anyway,
            so the write is pure cost.

    Returns:
        A :class:`MemoryWriteDecision` carrying both the verdict and a
        machine-readable reason, so the decision is auditable in logs.
    """
    text = (user_text or "").strip()
    if not text:
        return MemoryWriteDecision(False, "empty_user_message")

    # Repetition is checked first, before intent: it is the only rule that
    # saves money on a message that *would* otherwise be written, and mem0
    # would deduplicate the result anyway.
    if repeated:
        return MemoryWriteDecision(False, "repeat_of_persisted_memory")

    for pattern in _CHATTER_PATTERNS:
        if pattern.search(text):
            return MemoryWriteDecision(False, "chatter_or_bare_question")

    if len(text) < _MIN_INTENT_CHARS:
        return MemoryWriteDecision(False, "too_short")

    for pattern in _CORRECTION_PATTERNS:
        if pattern.search(text):
            return MemoryWriteDecision(True, "user_correction")

    for pattern in _INTENT_PATTERNS:
        if pattern.search(text):
            return MemoryWriteDecision(True, "stated_intent")

    # No intent marker, but a substantial exchange is still worth keeping: the
    # user may be stating a fact in a phrasing this list does not anticipate.
    # Bias toward writing, because a missed memory is a permanently missing
    # personalisation, while a redundant one is deduped downstream.
    if len(text) >= 60:
        return MemoryWriteDecision(True, "substantive_unclassified")

    return MemoryWriteDecision(False, "no_intent_marker")


# ── Confidence lifecycle ────────────────────────────────────────────────────

#: Confidence floor. A memory never decays to zero usefulness, it just stops
#: being trusted as much, so a forgotten preference can still resurface.
CONFIDENCE_FLOOR = 0.2

#: Half-life for a memory that is never recalled. A month of silence halves
#: its confidence, which is slow enough that an active user's preferences stay
#: pinned and fast enough that one-off context from months ago does not
#: masquerade as a standing preference.
DEFAULT_HALF_LIFE_DAYS = 30.0

#: How much a single recall is worth. Small on purpose: reinforcement should
#: take repeated use to matter, not a single lucky injection.
REINFORCEMENT_STEP = 0.05

#: Confidence granted by a fresh write. Previously a hardcoded 1.0, which is
#: the actual bug: nothing was ever allowed to be less than certain.
INITIAL_CONFIDENCE = 0.7


def _as_naive_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is not None:
        return value.astimezone(UTC).replace(tzinfo=None)
    return value


def decayed_confidence(
    confidence: float,
    last_used_at: datetime | None,
    updated_at: datetime | None = None,
    now: datetime | None = None,
    half_life_days: float = DEFAULT_HALF_LIFE_DAYS,
) -> float:
    """Exponential decay toward :data:`CONFIDENCE_FLOOR` for an unused memory.

    ``last_used_at`` is the timestamp of the most recent successful recall;
    when it is None the memory has never been used, so ``updated_at`` (when it
    was written or last touched) is used instead. Half-lives, not a cliff: the
    decay is continuous, so ordering by this value never ties arbitrarily.
    """
    if half_life_days <= 0:
        return max(CONFIDENCE_FLOOR, min(1.0, confidence))

    reference = _as_naive_utc(last_used_at) or _as_naive_utc(updated_at)
    if reference is None:
        return max(CONFIDENCE_FLOOR, min(1.0, confidence))

    now = _as_naive_utc(now) or datetime.now(UTC).replace(tzinfo=None)
    days = max(0.0, (now - reference).total_seconds() / 86400.0)
    # confidence_f = floor + (confidence_0 - floor) * 0.5 ** (days / half_life)
    decayed = CONFIDENCE_FLOOR + (confidence - CONFIDENCE_FLOOR) * math.pow(
        0.5, days / half_life_days
    )
    return max(CONFIDENCE_FLOOR, min(1.0, decayed))


def apply_reinforcement(
    confidence: float,
    step: float = REINFORCEMENT_STEP,
) -> float:
    """Return the confidence a memory earns by being recalled and useful.

    Capped at 1.0. Takes repeated use to matter, not a single lucky injection,
    so a memory cannot reach full trust on one retrieval.
    """
    return max(CONFIDENCE_FLOOR, min(1.0, confidence + step))


def expiry_for(
    confidence: float,
    now: datetime | None = None,
    half_life_days: float = DEFAULT_HALF_LIFE_DAYS,
) -> datetime | None:
    """When a memory will reach the floor, for opportunistic reaping.

    Returns None only when a memory is *already* at the floor, because such a
    memory is never scheduled again. A fully-trusted memory (confidence 1.0)
    does expire: the ratio is exactly 1.0, log2 gives 0 days, and it still needs
    a full half-life of disuse before it is worth retiring. Clamped to at least
    one half-life so "reaches the floor at" never degenerates to "expires now".
    """
    if confidence <= CONFIDENCE_FLOOR:
        return None

    headroom = 1.0 - CONFIDENCE_FLOOR
    if headroom <= 0:
        return None
    days = half_life_days * math.log2(
        max((confidence - CONFIDENCE_FLOOR) / headroom, sys.float_info.min)
    )
    days = max(days, half_life_days)
    now = _as_naive_utc(now) or datetime.now(UTC).replace(tzinfo=None)
    return now + timedelta(days=days)


# ── mem0 / user_memories reconciliation ─────────────────────────────────────

_NON_WORD = re.compile(r"[^\w\s]")
_WS = re.compile(r"\s+")


def normalize_for_dedupe(content: str) -> str:
    """Canonical form used to compare a mem0 fact against a stored row.

    mem0 and the table receive differently-formatted text for the same fact
    ("Prefers Python" vs "user prefers python."), so a raw string comparison
    never matches and the same memory is mirrored repeatedly. Casefolded,
    punctuation-stripped, whitespace-collapsed.
    """
    text = _NON_WORD.sub(" ", (content or "").casefold())
    return _WS.sub(" ", text).strip()


def is_duplicate(candidate: str, existing_contents: list[str]) -> bool:
    """True when ``candidate`` matches an already-persisted memory.

    Exact match on the normalised form. This is intentionally not fuzzy: a
    near-duplicate is mem0's job (it owns the semantic index), and a fuzzy
    check here would risk dropping a genuinely new fact.
    """
    target = normalize_for_dedupe(candidate)
    if not target:
        return True
    return any(normalize_for_dedupe(c) == target for c in existing_contents if c)


def partition_new_memories(
    candidates: list[dict[str, str]], existing_contents: list[str]
) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    """Split extracted facts into (new, duplicates), also deduping internally.

    mem0 can return the same fact twice in one batch, so the seen-set is
    seeded with what is already stored *and* grows as new facts are accepted.
    """
    seen = {normalize_for_dedupe(c) for c in existing_contents if c}
    new: list[dict[str, str]] = []
    dupes: list[dict[str, str]] = []
    for item in candidates:
        text = item.get("memory") or item.get("text") or ""
        key = normalize_for_dedupe(text)
        if not key or key in seen:
            dupes.append(item)
            continue
        seen.add(key)
        new.append(item)
    return new, dupes
