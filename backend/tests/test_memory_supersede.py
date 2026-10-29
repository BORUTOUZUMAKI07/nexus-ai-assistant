"""
Memory supersede -- context-drift resolution (Gap 3).

Dedupe collapses *identical* facts; it cannot collapse *contradictory* ones.
"My timezone is EST" and "my timezone is PST" are not duplicates, so before this
both stayed active and the model was handed two contradictory memories with no
way to prefer the newer. mem0's semantic dedupe has the same blind spot.

The rule implemented is deliberately narrow -- possessive attributes only --
because a verb-keyed rule would retire "I prefer tabs" when "I prefer dark mode"
arrived. These tests pin the sound cases *and* the cases the rule must refuse.
"""
from __future__ import annotations

from uuid import uuid4

import pytest
from backend.app.domain.user.models import UserMemory
from backend.app.domain.user.repository import UserRepository
from backend.app.services.memory_lifecycle import (
    fact_slot,
    find_superseded,
    partition_new_memories_with_supersede,
)
from backend.tests.fakes import FakeSession

# ── fact_slot ────────────────────────────────────────────────────────────────


def test_fact_slot_extracts_a_possessive_attribute():
    assert fact_slot("My timezone is EST") == ("timezone", "est")


def test_fact_slot_tolerates_casing_punctuation_and_plurals():
    assert fact_slot("Our  preferred stack is React, actually.") == (
        "preferred stack",
        "react actually",
    )


def test_fact_slot_ignores_a_non_possessive_statement():
    assert fact_slot("I prefer tabs") is None
    assert fact_slot("The server is down") is None


# ── find_superseded ──────────────────────────────────────────────────────────


def test_a_new_value_supersedes_the_old_one():
    existing = ["My timezone is EST", "My editor is vim"]
    assert find_superseded("My timezone is PST", existing) == "My timezone is EST"


def test_an_identical_value_supersedes_nothing():
    existing = ["My timezone is EST"]
    assert find_superseded("My timezone is EST", existing) is None


def test_a_different_slot_supersedes_nothing():
    """The guard against the "I prefer tabs" over-reach."""
    existing = ["My language is Python"]
    assert find_superseded("My editor is vim", existing) is None


def test_a_non_possessive_candidate_supersedes_nothing():
    existing = ["My timezone is EST"]
    assert find_superseded("I moved to PST", existing) is None


# ── partition_new_memories_with_supersede ────────────────────────────────────


def test_partition_reports_the_replaced_existing_fact():
    new, dupes, superseded = partition_new_memories_with_supersede(
        [{"memory": "My timezone is PST"}],
        ["My timezone is EST"],
    )
    assert [f["memory"] for f in new] == ["My timezone is PST"]
    assert dupes == []
    assert superseded == ["My timezone is EST"]


def test_partition_keeps_dedupe_behaviour_unchanged():
    new, dupes, superseded = partition_new_memories_with_supersede(
        [{"memory": "Prefers Python."}],
        ["prefers python"],
    )
    assert new == []
    assert len(dupes) == 1
    assert superseded == []


def test_partition_last_value_wins_within_one_batch():
    new, _dupes, superseded = partition_new_memories_with_supersede(
        [{"memory": "My budget is $500"}, {"memory": "My budget is $1,000"}],
        [],
    )
    assert [f["memory"] for f in new] == ["My budget is $1,000"]
    assert superseded == ["My budget is $500"]


def test_partition_never_supersedes_a_survivor():
    """A fact that is still being written must not also be reported retired."""
    new, _dupes, superseded = partition_new_memories_with_supersede(
        [{"memory": "My timezone is PST"}],
        ["My timezone is PST"],
    )
    assert new == []
    assert superseded == []


# ── repository wiring ────────────────────────────────────────────────────────


async def test_mirror_memories_deactivates_the_superseded_row():
    session = FakeSession()
    user_id = uuid4()
    old = UserMemory(user_id=user_id, content="My timezone is EST", is_active=True)
    session.seed(UserMemory, [old])

    created, skipped = await UserRepository(session).mirror_memories(
        user_id, [{"memory": "My timezone is PST"}]
    )

    assert skipped == 0
    assert [m.content for m in created] == ["My timezone is PST"]
    assert old.is_active is False, "the stale value must be retired"


async def test_mirror_memories_leaves_an_unrelated_fact_alone():
    session = FakeSession()
    user_id = uuid4()
    old = UserMemory(user_id=user_id, content="My language is Python", is_active=True)
    session.seed(UserMemory, [old])

    await UserRepository(session).mirror_memories(
        user_id, [{"memory": "My editor is vim"}]
    )

    assert old.is_active is True


async def test_a_failed_write_does_not_retire_the_old_fact(monkeypatch):
    """Retire-then-write loses the fact when the replacement never lands."""
    session = FakeSession()
    user_id = uuid4()
    old = UserMemory(user_id=user_id, content="My timezone is EST", is_active=True)
    session.seed(UserMemory, [old])

    async def boom(self, **kwargs):
        raise RuntimeError("write failed")

    monkeypatch.setattr(UserRepository, "create_memory", boom)

    with pytest.raises(RuntimeError):
        await UserRepository(session).mirror_memories(
            user_id, [{"memory": "My timezone is PST"}]
        )

    assert old.is_active is True, "a failed replacement must not retire the old fact"


async def test_deactivate_matches_on_the_normalised_form():
    session = FakeSession()
    user_id = uuid4()
    stale = UserMemory(user_id=user_id, content="My timezone is EST.", is_active=True)
    session.seed(UserMemory, [stale])

    count = await UserRepository(session).deactivate_memories_by_contents(
        user_id, ["my timezone is est"]
    )

    assert count == 1
    assert stale.is_active is False


async def test_deactivate_never_touches_another_users_memory():
    session = FakeSession()
    owner = uuid4()
    other = UserMemory(user_id=uuid4(), content="My timezone is EST", is_active=True)
    session.seed(UserMemory, [other])

    count = await UserRepository(session).deactivate_memories_by_contents(
        owner, ["My timezone is EST"]
    )

    assert count == 0
    assert other.is_active is True
