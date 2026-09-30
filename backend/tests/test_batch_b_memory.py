"""
Tests for Batch B — memory write gating, confidence lifecycle and the
mem0 <-> user_memories reconciliation.

Each test fails if its corresponding fix is reverted:
  * ``test_should_persist_*``   fail if the unconditional write comes back
  * ``test_decayed_*``          fail if confidence never falls again
  * ``test_mirror_*``           fail if extracted facts stop being persisted
"""
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from backend.app.services.memory_lifecycle import (
    CONFIDENCE_FLOOR,
    apply_reinforcement,
    decayed_confidence,
    expiry_for,
    is_duplicate,
    normalize_for_dedupe,
    partition_new_memories,
    should_persist_memory,
)

NOW = datetime(2026, 9, 30, 12, 0, 0)


# ── B1: the write gate ───────────────────────────────────────────────────────
#
# The bug: nodes.py wrote every assistant response to mem0, and mem0 runs an
# LLM extraction pass per write. These assert the expensive cases are refused.


def test_greeting_is_not_persisted():
    """The regression: "hi" used to cost an LLM extraction call."""
    for text in ("hi", "Hey!", "thanks", "thank you", "ok", "cool", "sure", "bye"):
        decision = should_persist_memory(text)
        assert not decision.should_write, f"{text!r} must not be persisted"
        assert decision.reason == "chatter_or_bare_question"


def test_bare_question_is_not_persisted():
    decision = should_persist_memory("What is the capital of France?")
    assert not decision.should_write
    assert decision.reason == "chatter_or_bare_question"


def test_empty_and_tiny_messages_are_not_persisted():
    assert not should_persist_memory("").should_write
    assert should_persist_memory("").reason == "empty_user_message"
    assert not should_persist_memory("ok!").should_write


def test_stated_preference_is_persisted():
    decision = should_persist_memory("I prefer dark mode in all my tools")
    assert decision.should_write
    assert decision.reason == "stated_intent"


def test_name_and_role_statements_are_persisted():
    for text in (
        "My name is Priya and I work on distributed systems",
        "Call me Sam from now on",
        "I'm a backend engineer based in Berlin",
        "We use Postgres for everything",
        "I always deploy on Fridays, never do that again",
        "From now on answer in bullet points only",
    ):
        assert should_persist_memory(text).should_write, f"{text!r} should persist"


def test_correction_is_persisted_as_high_value():
    """Fixing a wrong fact is the strongest possible memory signal."""
    for text in (
        "No, that's wrong, it's Postgres not MySQL",
        "Actually I told you I use Rust",
        "I meant the staging database",
        "Not quite, the answer is 42",
    ):
        decision = should_persist_memory(text)
        assert decision.should_write, f"{text!r} should persist"
        assert decision.reason == "user_correction"


def test_correction_beats_chatter_check():
    """A correction that begins with "no" must not be classified as chatter."""
    decision = should_persist_memory("No, that's wrong, I use Postgres not MySQL")
    assert decision.should_write
    assert decision.reason == "user_correction"


def test_repetition_is_refused_before_spending_a_call():
    """Repetition must be checked before intent, or the intent branch wins and
    the write is paid for anyway."""
    decision = should_persist_memory(
        "I prefer dark mode in all my tools", repeated=True
    )
    assert not decision.should_write
    assert decision.reason == "repeat_of_persisted_memory"


def test_substantial_unclassified_message_is_persisted():
    """Bias toward writing: a missed memory is permanent, a dup is deduped.

    This is the deliberate asymmetry. A user stating a fact in a phrasing the
    pattern list does not anticipate should still be remembered.
    """
    text = (
        "We are migrating the billing service off the monolith this quarter and "
        "the new owner is the payments team in Dublin"
    )
    decision = should_persist_memory(text)
    assert decision.should_write
    assert decision.reason == "substantive_unclassified"


def test_short_non_intent_is_refused():
    decision = should_persist_memory("the deploy went out at noon")
    assert not decision.should_write
    assert decision.reason == "no_intent_marker"


def test_gate_is_deterministic_and_pure():
    """No LLM call, no I/O: the same input always gives the same verdict."""
    text = "I prefer dark mode in all my tools"
    assert should_persist_memory(text).reason == should_persist_memory(text).reason


def test_decision_is_truthy_only_when_writable():
    assert bool(should_persist_memory("I prefer dark mode in all my tools")) is True
    assert bool(should_persist_memory("hi")) is False


# ── B2: confidence lifecycle ────────────────────────────────────────────────
#
# The bug: confidence defaulted to 1.0 and was never written again, so every
# memory was permanently maximally trusted.


def test_never_used_memory_decays():
    fresh = decayed_confidence(1.0, last_used_at=None, updated_at=NOW, now=NOW)
    assert fresh == 1.0, "a memory just written has not decayed yet"

    later = decayed_confidence(1.0, last_used_at=None, updated_at=NOW, now=NOW + timedelta(days=30))
    assert later < 1.0, "a month of disuse must reduce confidence"
    # 30 days = one half-life, so exactly halfway from 1.0 to the floor.
    expected = CONFIDENCE_FLOOR + (1.0 - CONFIDENCE_FLOOR) * 0.5
    assert abs(later - expected) < 1e-9


def test_decay_is_bounded_at_the_floor():
    ancient = decayed_confidence(
        1.0, last_used_at=None, updated_at=NOW, now=NOW + timedelta(days=3650)
    )
    assert ancient == CONFIDENCE_FLOOR, "must never fall below the floor"


def test_decay_uses_last_use_not_last_write():
    """A memory that keeps being recalled must not decay while it is in use."""
    used_recently = decayed_confidence(
        0.8,
        last_used_at=NOW,
        updated_at=NOW - timedelta(days=300),
        now=NOW,
    )
    assert used_recently == 0.8, "recent recall means no decay"


def test_decay_never_exceeds_one_or_drops_below_floor():
    for days in (0, 1, 7, 30, 90, 365, 3650):
        value = decayed_confidence(1.0, None, NOW, now=NOW + timedelta(days=days))
        assert CONFIDENCE_FLOOR <= value <= 1.0


def test_future_timestamps_do_not_inflate_confidence():
    """Clock skew must not hand a memory extra confidence."""
    value = decayed_confidence(0.5, last_used_at=None, updated_at=NOW + timedelta(days=5), now=NOW)
    assert value == 0.5


def test_half_life_of_zero_short_circuits():
    assert decayed_confidence(0.9, None, NOW, now=NOW, half_life_days=0) == 0.9


def test_reinforcement_raises_confidence_but_stops_at_one():
    assert apply_reinforcement(0.5) > 0.5
    assert apply_reinforcement(0.99) <= 1.0
    assert apply_reinforcement(1.0) == 1.0


def test_reinforcement_never_below_floor():
    assert apply_reinforcement(0.0) >= CONFIDENCE_FLOOR


def test_repeated_reinforcement_converges_to_full_trust():
    """Memories that prove useful should eventually be fully trusted."""
    value = 0.7
    for _ in range(20):
        value = apply_reinforcement(value)
    assert value == 1.0


def test_expiry_is_none_at_the_floor_and_in_the_future_otherwise():
    assert expiry_for(CONFIDENCE_FLOOR, now=NOW) is None
    when = expiry_for(1.0, now=NOW)
    assert when is not None and when > NOW


# ── B3: mem0 <-> table reconciliation ───────────────────────────────────────
#
# The bug: memory.py (mem0) and user/repository.py (the table) were two
# independent stores of the same facts that never exchanged anything.


def test_normalisation_ignores_case_punctuation_and_whitespace():
    assert normalize_for_dedupe("Prefers Python!") == normalize_for_dedupe(
        "  prefers   python.  "
    )


def test_dedupe_detects_the_same_fact_written_differently():
    existing = ["User prefers Python", "user  works at Acme Corp!"]
    assert is_duplicate("User prefers python", existing)
    assert is_duplicate("user works at ACME   corp", existing)
    assert not is_duplicate("User prefers Rust", existing)


def test_dedupe_is_exact_not_substring():
    """A near-duplicate is mem0's job; a fuzzy check here would drop real facts.

    "prefers python" is a substring of "user prefers python" but they are not
    the same fact, so the exact comparison must not treat them as equal.
    """
    assert not is_duplicate("prefers python", ["User prefers Python"])
    assert not is_duplicate("prefers", ["User prefers Python"])


def test_dedupe_treats_empty_as_duplicate():
    """An empty candidate must never be mirrored as a new memory."""
    assert is_duplicate("", ["anything"])
    assert is_duplicate("   ", [])


def test_partition_splits_new_from_duplicate():
    facts = [
        {"memory": "User prefers Python"},
        {"memory": "user works at Acme"},
        {"memory": "User prefers python."},  # dup of the first
    ]
    new, dupes = partition_new_memories(facts, ["User prefers Python"])
    assert [f["memory"] for f in new] == ["user works at Acme"]
    assert len(dupes) == 2


def test_partition_dedupes_within_one_batch():
    """mem0 can return the same fact twice in a single response."""
    facts = [{"memory": "Likes tea"}, {"memory": "likes  tea"}]
    new, dupes = partition_new_memories(facts, [])
    assert len(new) == 1
    assert len(dupes) == 1


def test_partition_ignores_facts_with_no_text():
    facts = [{"memory": ""}, {"memory": "   "}, {"id": "x"}, {"text": "Real fact"}]
    new, dupes = partition_new_memories(facts, [])
    assert [f.get("text") for f in new] == ["Real fact"]
    assert len(dupes) == 3


def test_partition_reads_both_mem0_text_shapes():
    new, _ = partition_new_memories([{"text": "via text key"}], [])
    assert len(new) == 1


# ── B1+B3 wiring: the synthesizer gate and the mirror helper ────────────────


def _synth_state(user_text: str):
    return {
        "messages": [_Msg(user_text, "human")],
        "user_id": "u1",
        "conversation_id": "c1",
        "trace_id": "",
        "mode": "normal",
        "system_prompt": "SYSTEM",
        "task_type": "general",
        "revision_count": 0,
        "citations": [],
    }


class _Msg:
    def __init__(self, content: str, msg_type: str = "human") -> None:
        self.content = content
        self.type = msg_type


class _NoCritic:
    async def evaluate(self, **kwargs):
        return {"approved": True, "critique": ""}


async def _run_synth(monkeypatch, nodes, user_text: str) -> dict:
    calls: list[dict] = []

    async def fake_completion(**kwargs):
        calls.append(kwargs)
        return "A response."

    async def fake_add(**kwargs):
        calls.append({"mem0": kwargs})
        return [{"id": "m1", "memory": "User prefers Python"}]

    monkeypatch.setattr(nodes.ai_client, "completion", fake_completion)
    monkeypatch.setattr(nodes, "critic_subagent", _NoCritic())
    monkeypatch.setattr(nodes.long_term_memory, "add_from_conversation", fake_add)
    await nodes.synthesizer_node(_synth_state(user_text))
    return {"calls": calls}


async def test_synthesizer_skips_mem0_write_for_chatter(monkeypatch):
    """The regression: every response used to be written to mem0."""
    from backend.app.agents.orchestrator import nodes

    result = await _run_synth(monkeypatch, nodes, "thanks!")
    assert not any("mem0" in c for c in result["calls"]), "chatter must not cost a write"


async def test_synthesizer_still_writes_for_stated_intent(monkeypatch):
    from backend.app.agents.orchestrator import nodes

    result = await _run_synth(monkeypatch, nodes, "I prefer dark mode in all my tools")
    assert any("mem0" in c for c in result["calls"]), "a stated preference must be persisted"


async def test_synthesizer_mirrors_extracted_facts_to_the_table(monkeypatch):
    """The regression: mem0 memories were invisible to every local query."""
    from backend.app.agents.orchestrator import nodes

    seen: list[dict] = {}

    async def fake_mirror(user_id, facts, conversation_id=None):
        seen["facts"] = facts
        return len(facts)

    monkeypatch.setattr(nodes, "_mirror_memories_to_table", fake_mirror)
    await _run_synth(monkeypatch, nodes, "I prefer dark mode in all my tools")
    assert seen["facts"] == [{"id": "m1", "memory": "User prefers Python"}]


async def test_synthesizer_does_not_mirror_when_nothing_was_extracted(monkeypatch):
    """An empty extraction must not open a database session for nothing."""
    from backend.app.agents.orchestrator import nodes

    async def fake_add(**kwargs):
        return []

    called = False

    async def fake_mirror(*a, **k):
        nonlocal called
        called = True
        return 0

    monkeypatch.setattr(nodes.long_term_memory, "add_from_conversation", fake_add)
    monkeypatch.setattr(nodes, "_mirror_memories_to_table", fake_mirror)
    monkeypatch.setattr(nodes, "critic_subagent", _NoCritic())

    async def fake_completion(**kwargs):
        return "A response."

    monkeypatch.setattr(nodes.ai_client, "completion", fake_completion)
    await nodes.synthesizer_node(_synth_state("I prefer dark mode in all my tools"))
    assert called is False


async def test_mirror_helper_fails_open(monkeypatch):
    """A database outage must not fail the user's turn."""
    from backend.app.agents.orchestrator import nodes

    class Boom:
        async def __aenter__(self):
            raise RuntimeError("db down")

        async def __aexit__(self, *a):
            return False

    monkeypatch.setattr(
        "backend.app.infrastructure.database.session.async_session_factory", lambda: Boom()
    )
    assert (
        await nodes._mirror_memories_to_table(str(uuid4()), [{"memory": "x"}], "c1")
        == 0
    )


async def test_mirror_helper_tolerates_a_non_uuid_conversation_id(monkeypatch):
    """conversation_id is a free-form string in the graph state; a bad value
    must not stop the mirror — the fact is already safely in mem0."""
    from uuid import uuid4

    from backend.app.agents.orchestrator import nodes

    seen: dict = {}

    class FakeRepo:
        def __init__(self, session):
            pass

        async def mirror_memories(self, user_id, facts, source_conv_id=None, scope="user"):
            seen["source_conv_id"] = source_conv_id
            return [], 1

    class Ok:
        async def __aenter__(self):
            return object()

        async def __aexit__(self, *a):
            return False

    monkeypatch.setattr(
        "backend.app.infrastructure.database.session.async_session_factory", lambda: Ok()
    )
    monkeypatch.setattr(
        "backend.app.domain.user.repository.UserRepository", FakeRepo
    )
    assert (
        await nodes._mirror_memories_to_table(str(uuid4()), [{"memory": "x"}], "not-a-uuid")
        == 0
    )
    assert seen["source_conv_id"] is None


async def test_mirror_helper_refuses_a_malformed_user_id(monkeypatch):
    """A bad user_id cannot be silently coerced — the mirror is skipped.

    The fact remains in mem0, so this costs a duplicate, not a lost memory.
    """
    from backend.app.agents.orchestrator import nodes

    opened = False

    class Ok:
        async def __aenter__(self):
            nonlocal opened
            opened = True
            return object()

        async def __aexit__(self, *a):
            return False

    monkeypatch.setattr(
        "backend.app.infrastructure.database.session.async_session_factory", lambda: Ok()
    )
    assert await nodes._mirror_memories_to_table("u1", [{"memory": "x"}], "c1") == 0
    assert opened is False, "must not open a session it cannot use"


# ── Model defaults ──────────────────────────────────────────────────────────


def test_user_memory_no_longer_defaults_to_certainty():
    """The bug: confidence defaulted to 1.0 and was never lowered."""
    from backend.app.domain.user.models import UserMemory

    assert UserMemory.model_fields["confidence"].default < 1.0
    assert UserMemory.model_fields["retrieval_count"].default == 0
    assert UserMemory.model_fields["scope"].default == "user"
    assert "mem0_id" in UserMemory.model_fields
    assert "last_used_at" in UserMemory.model_fields


def test_memory_write_decision_repr_is_informative():
    decision = should_persist_memory("hi")
    assert "chatter" in repr(decision)
