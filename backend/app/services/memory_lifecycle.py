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

4. Dedupe could not resolve *context drift*. "My timezone is EST" and "my
   timezone is PST" are not duplicates, so both stayed active and the model was
   handed two contradictory memories. :func:`partition_new_memories_with_supersede`
   and :func:`find_superseded` implement latest-wins replacement for possessive
   attributes -- still entirely LLM-free, as the hot path requires.
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


# ── Supersede (context drift) ────────────────────────────────────────────────
#
# Dedupe answers "have I seen this exact fact?". It cannot answer "is this a
# *newer* value for a fact I already hold?". "My timezone is EST" and "my
# timezone is PST" normalise differently, so both survive -- and the model is
# then handed two contradictory memories with no way to prefer the newer one.
# That is context drift, and it is what the long-term-memory lesson is about:
# mem0's semantic dedupe collapses *similar* facts, not *contradictory* ones.
#
# The rule is deliberately restricted to possessive attributes ("my timezone is
# X", "our stack is Y"). Keying on a verb would be unsafe: "I prefer tabs" and
# "I prefer dark mode" share the verb "prefer" but are independent facts, so a
# verb-keyed rule would retire one the moment the other arrived. A possessive
# attribute names the *slot* the value fills, which is exactly what makes
# replacement sound -- and, per this module's bias, a missed supersede is a
# stale memory (recoverable, the user re-states it) while a wrong supersede
# deletes a fact the user never retracted.

_POSSESSIVE_ATTRIBUTE = re.compile(
    r"\b(?:my|our)\s+([a-z][a-z0-9 '\-]{0,40}?)\s+(?:is|are|was|were)\s+(.+)",
    re.IGNORECASE,
)


def fact_slot(content: str) -> tuple[str, str] | None:
    """Return ``(slot, value)`` for a possessive-attribute fact, else ``None``.

    ``"My timezone is EST"`` -> ``("timezone", "est")``. The slot is what makes
    replacement sound: two facts fill the same slot only when they describe the
    same attribute.
    """
    match = _POSSESSIVE_ATTRIBUTE.search(content or "")
    if not match:
        return None
    slot = normalize_for_dedupe(match.group(1))
    value = normalize_for_dedupe(match.group(2))
    if not slot or not value:
        return None
    return slot, value


def find_superseded(candidate: str, existing_contents: list[str]) -> str | None:
    """Return the existing memory ``candidate`` replaces, or ``None``.

    Replacement requires the same slot with a *different* value. Returns the
    first such stored content; the caller deactivates it. A candidate that
    matches nothing -- or matches only by exact dedupe -- supersedes nothing.
    """
    current = fact_slot(candidate)
    if current is None:
        return None
    slot, value = current
    for existing in existing_contents:
        if not existing:
            continue
        other = fact_slot(existing)
        if other is not None and other[0] == slot and other[1] != value:
            return existing
    return None


def partition_new_memories_with_supersede(
    candidates: list[dict[str, str]],
    existing_contents: list[str],
) -> tuple[list[dict[str, str]], list[dict[str, str]], list[str]]:
    """Like :func:`partition_new_memories`, plus the facts a survivor replaces.

    Returns ``(new, duplicates, superseded_contents)``. ``new`` and
    ``duplicates`` are exactly what :func:`partition_new_memories` returns, so
    this is a strict superset rather than a second reconciliation policy.
    ``superseded_contents`` are existing memory strings a surviving candidate
    replaces; the caller deactivates them.

    Last-wins within one batch: if the model extracted two values for one slot
    in a single pass, the later survives and the earlier is reported as
    superseded. That earlier fact may not exist in the store yet, in which case
    the caller's deactivation is a harmless no-op.
    """
    new, dupes = partition_new_memories(candidates, existing_contents)
    superseded: list[str] = []
    survivors: list[dict[str, str]] = []
    slot_index: dict[str, int] = {}

    for item in new:
        text = (item.get("memory") or item.get("text") or "").strip()
        slot = fact_slot(text)
        if slot is not None and slot[0] in slot_index:
            previous = survivors[slot_index[slot[0]]]
            superseded.append((previous.get("memory") or previous.get("text") or "").strip())
            survivors[slot_index[slot[0]]] = item
        else:
            if slot is not None:
                slot_index[slot[0]] = len(survivors)
            survivors.append(item)

        replaced = find_superseded(text, existing_contents)
        if replaced is not None:
            superseded.append(replaced)

    # Never report a fact as superseded while it still appears among the
    # survivors, and de-duplicate (two candidates can replace one stored fact).
    surviving = {
        normalize_for_dedupe(s.get("memory") or s.get("text") or "") for s in survivors
    }
    seen: set[str] = set()
    unique: list[str] = []
    for content in superseded:
        key = normalize_for_dedupe(content)
        if not key or key in seen or key in surviving:
            continue
        seen.add(key)
        unique.append(content)
    return survivors, dupes, unique


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
