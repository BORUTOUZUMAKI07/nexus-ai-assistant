"""Tests for answer-coverage grading (services/rag/answer_coverage.py).

This stage exists to catch one specific failure: a passage about the wrong
version of the right thing scoring well on similarity and being cited as though
it answered the question. Every test here is about which direction the stage
fails in, because both directions are reachable and only one of them is
recoverable. Citing something redundant is embarrassing; answering from nothing
while showing an empty citations panel is a confident wrong answer.
"""
from __future__ import annotations

import math
from typing import Any

import pytest
from backend.app.core.config import settings
from backend.app.services.rag import answer_coverage
from backend.app.services.rag.answer_coverage import (
    grade_answer_coverage,
)


class DecisionClient:
    """Returns a scripted label per graded chunk, and counts the calls.

    Counts matter: "which chunks get graded" is half of what this stage does, and
    a fake that ignores the question cannot answer that.
    """

    def __init__(self, script: list[tuple[str, dict[str, float] | None]]):
        self.script = script
        self.seen: list[str] = []

    async def complete(self, **kwargs: Any) -> dict[str, Any]:
        self.seen.append(kwargs["messages"][-1]["content"])
        label, distribution = self.script[len(self.seen) - 1]
        if distribution is None:
            return {"content": label, "logprobs": None}
        return {
            "content": label,
            "logprobs": [
                {
                    "token": label,
                    "logprob": -0.01,
                    "top": [(name, value) for name, value in distribution.items()],
                }
            ],
        }


def chunk(index: int, text: str = "passage") -> dict[str, Any]:
    return {"id": f"c{index}", "content": text, "score": 1.0 - index / 100}


def measured(label: str, probability: float, *, others: tuple[str, ...] = ()) -> tuple[str, dict[str, float] | None]:
    """A measured decision carrying `probability` mass on `label`.

    A provider's `top_logprobs` are **log**-probabilities and `_score_labels`
    exponentiates them, so passing the probabilities straight through would make
    the fake describe a different model than the test claims -- the first version
    of this helper asked for 0.61 and the grader duly reported 0.43, which six
    tests then inherited. Converting here is what makes the fixture mean what it
    says.
    """
    assert 0.0 < probability < 1.0
    distribution = {label: probability}
    for other in others:
        distribution[other] = (1.0 - probability) / max(1, len(others))
    logprobs = {name: math.log(value) for name, value in distribution.items()}
    return label, logprobs


def ruled(label: str) -> tuple[str, dict[str, float] | None]:
    """A provider that answered but supplied no scores.

    Two consequences, and they are different: with no rule supplied this is
    `None` from `decide()` and lands in `undecided`; with a rule it is an
    unmeasured decision recorded as `source=deterministic`. Conflating them is
    what would make the report unusable for judging the feature.
    """
    return label, None


def rule_answers(state: str, question: Any) -> str:
    """The shape of a real fallback rule: cheap, and with no confidence at all.

    Keyword matching over the graded text, which is what an actual fallback for
    this stage would look like. A constant would not do: with a constant, any
    test that only checks the *label* would still pass if the grader inverted
    the precedence between a measurement and a rule.
    """
    lowered = state.lower()
    if "does_not_answer" in lowered or "irrelevant" in lowered:
        return "does_not_answer"
    return "partially"


# Short alias so the tests read cleanly.
rule_no = rule_answers


@pytest.fixture
def enabled(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(settings, "RAG_ANSWER_COVERAGE_ENABLED", True)


class TestTheFeatureFlag:
    @pytest.mark.asyncio
    async def test_disabled_by_default_so_no_query_pays_for_it(self, monkeypatch: pytest.MonkeyPatch):
        """This adds a model call per graded chunk on the query path.

        Whether that buys more than it costs is measurable only against real
        queries, so the switch is the user's, not a default I picked for them.
        """
        assert settings.RAG_ANSWER_COVERAGE_ENABLED is False

    @pytest.mark.asyncio
    async def test_disabled_returns_the_chunks_untouched(self):
        chunks = [chunk(0), chunk(1)]
        client = DecisionClient([measured("does_not_answer", 0.9, others=("answers",))])

        kept, report = await grade_answer_coverage("q", chunks, client=client)

        assert kept == chunks
        assert report["enabled"] is False
        assert report["graded"] == 0
        assert client.seen == [], "a disabled stage must not spend a call"

    @pytest.mark.asyncio
    async def test_enabled_but_no_client_degrades_to_a_no_op(self, enabled: None):
        """An absent client means the stage cannot measure, so nothing is dropped.

        Treating "could not grade" as "does not answer" would strip evidence on
        every deployment where the client failed to resolve.
        """
        chunks = [chunk(0), chunk(1)]
        kept, report = await grade_answer_coverage("q", chunks, client=None)

        assert kept == chunks
        assert report["graded"] == 0


class TestDropping:
    @pytest.mark.asyncio
    async def test_a_measured_non_answer_is_dropped(self, enabled: None):
        chunks = [chunk(0), chunk(1)]
        client = DecisionClient([
            measured("does_not_answer", 0.94, others=("answers", "partially")),
            measured("answers", 0.91, others=("partially", "does_not_answer")),
        ])

        kept, report = await grade_answer_coverage("q", chunks, client=client)

        assert [c["id"] for c in kept] == ["c1"]
        assert report["dropped"] == 1
        assert report["graded"] == 2

    @pytest.mark.asyncio
    async def test_a_measured_answer_is_kept(self, enabled: None):
        chunks = [chunk(0)]
        client = DecisionClient([measured("answers", 0.88, others=("partially", "does_not_answer"))])

        kept, _ = await grade_answer_coverage("q", chunks, client=client)

        assert [c["id"] for c in kept] == ["c0"]

    @pytest.mark.asyncio
    async def test_a_confident_non_answer_is_dropped_even_above_the_partial_threshold(self, enabled: None):
        """`does_not_answer` clears a lower bar than `partially` on purpose.

        A passage that positively does not answer is a distractor; a passage that
        answers in part is still evidence. Comparing both against one threshold
        would either keep confident distractors or discard partial answers.
        """
        assert settings.RAG_ANSWER_COVERAGE_DROP_BELOW > 0.5

        chunks = [chunk(0), chunk(1)]
        client = DecisionClient([
            measured("does_not_answer", 0.95, others=("answers", "partially")),
            measured("partially", 0.97, others=("answers", "does_not_answer")),
        ])

        kept, report = await grade_answer_coverage("q", chunks, client=client)

        assert [c["id"] for c in kept] == ["c1"]
        assert report["dropped"] == 1

    @pytest.mark.asyncio
    async def test_a_weak_partial_is_dropped_below_the_threshold(self, enabled: None, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr(settings, "RAG_ANSWER_COVERAGE_DROP_BELOW", 0.75)
        # Three chunks, two graded: the third is an ungraded tail that survives,
        # so the empty-guard has nothing to do and cannot mask a broken drop.
        # With two chunks and no tail the guard restores chunk 0 and this test
        # passes with the drop logic entirely removed -- the §9.13 trap, where
        # each half of a compound condition must be independently load-bearing.
        chunks = [chunk(0), chunk(1), chunk(2)]
        client = DecisionClient([
            measured("partially", 0.61, others=("answers", "does_not_answer")),
            measured("partially", 0.61, others=("answers", "does_not_answer")),
        ])

        kept, report = await grade_answer_coverage("q", chunks, client=client, top_n=2)

        assert [c["id"] for c in kept] == ["c2"], "both graded weak partials should be dropped"
        assert report["dropped"] == 2
        assert report["empty_guard_fired"] is False

    @pytest.mark.asyncio
    async def test_a_weak_partial_is_kept_at_or_above_the_threshold(self, enabled: None, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr(settings, "RAG_ANSWER_COVERAGE_DROP_BELOW", 0.5)
        chunks = [chunk(0)]
        client = DecisionClient([measured("partially", 0.55, others=("answers", "does_not_answer"))])

        kept, _ = await grade_answer_coverage("q", chunks, client=client)

        assert [c["id"] for c in kept] == ["c0"]


class TestWhatMustNeverBeDropped:
    @pytest.mark.asyncio
    async def test_an_unmeasured_decision_never_drops_anything(self, enabled: None):
        """The core safety invariant of this stage.

        A decision that fell back to a rule has no probability. Treating its
        label as though it had one -- or, worse, treating "no measurement" as
        "does not answer" -- would let every provider without logprobs delete
        evidence wholesale, and the feature would look like it was working.
        """
        chunks = [chunk(0), chunk(1)]
        client = DecisionClient([ruled("does_not_answer"), ruled("does_not_answer")])

        # The rule matters here, twice over. Without one, `decide()` returns
        # None and the chunk never reaches the drop decision at all, so the test
        # would exercise the *absence* of a decision rather than an unmeasured
        # one. And the rule must return `does_not_answer`, not `partially`: an
        # unmeasured `partially` is kept by the threshold comparison regardless
        # of the guard, so the guard was invisible to it. This is the §9.13 trap
        # again -- the first version of this test passed with the guard deleted.
        def rule(state: str, question: Any) -> str:
            return "does_not_answer"

        kept, report = await grade_answer_coverage(
            "q", chunks, client=client, deterministic=rule
        )

        assert kept == chunks
        assert report["dropped"] == 0
        assert [record["label"] for record in report["decisions"]] == ["does_not_answer"] * 2
        assert [record["source"] for record in report["decisions"]] == ["deterministic"] * 2
        assert report["graded"] == 2
        assert report["measured"] == 0
        assert all(record["source"] != "logprobs" for record in report["decisions"])

    @pytest.mark.asyncio
    async def test_a_decision_that_could_not_be_made_keeps_its_chunk(self, enabled: None):
        """No logprobs and no rule means `decide()` returns None, not a label.

        The chunk still has to survive, and the run has to be distinguishable
        from one where grading happened. `undecided` is the only thing that tells
        an operator the provider is not giving us scores -- without it a fully
        failed deployment reports a clean run with nothing graded and nothing
        dropped, which reads as "this feature costs nothing and does nothing".
        """
        # Three chunks and three undecidables, so the empty-guard cannot explain the
        # surviving evidence. With a single chunk, deleting the `kept.append`
        # leaves `result` empty, the guard fires and restores chunk 0, and the
        # test passes with the keep behaviour entirely removed -- which is
        # exactly what the revert harness reported until the fixture grew.
        chunks = [chunk(0), chunk(1), chunk(2)]
        client = DecisionClient([ruled("yes"), ruled("yes"), ruled("yes")])

        kept, report = await grade_answer_coverage("q", chunks, client=client)

        assert [c["id"] for c in kept] == ["c0", "c1", "c2"]
        assert report["graded"] == 3
        assert report["undecided"] == 3
        assert report["empty_guard_fired"] is False
        assert report["measured"] == 0
        assert report["dropped"] == 0
        assert report["decisions"] == []

    @pytest.mark.asyncio
    async def test_the_stage_never_returns_no_evidence_at_all(self, enabled: None):
        """If every graded chunk is a measured non-answer, keep the best one.

        That outcome means the grader and the retriever disagree about the
        question, which is a fact about the grader. Discarding everything would
        hand the synthesizer nothing and let it answer from its own priors while
        the citations panel sits empty -- the exact ungrounded answer this stage
        exists to prevent.
        """
        chunks = [chunk(0), chunk(1), chunk(2)]
        client = DecisionClient([
            measured("does_not_answer", 0.99, others=("answers", "partially")) for _ in range(3)
        ])

        kept, report = await grade_answer_coverage("q", chunks, client=client)

        assert [c["id"] for c in kept] == ["c0"], "the top-ranked chunk should be restored"
        assert report["empty_guard_fired"] is True
        # The report has to say so, or the anomaly is invisible.
        assert report["decisions"][0]["dropped"] is False
        assert report["decisions"][1]["dropped"] is True

    @pytest.mark.asyncio
    async def test_the_empty_guard_keeps_the_tail_before_it_rescues_anything(self, enabled: None):
        """Ungraded chunks are not evidence the grader has rejected.

        A tail chunk survives by never being examined, which is different from
        being found wanting, so the guard should have nothing to do.
        """
        chunks = [chunk(0), chunk(1), chunk(2), chunk(3)]
        client = DecisionClient([
            measured("does_not_answer", 0.99, others=("answers", "partially")),
            measured("does_not_answer", 0.99, others=("answers", "partially")),
        ])

        kept, report = await grade_answer_coverage("q", chunks, client=client, top_n=2)

        assert [c["id"] for c in kept] == ["c2", "c3"]
        assert report["empty_guard_fired"] is False

    @pytest.mark.asyncio
    async def test_the_empty_guard_stays_off_when_nothing_was_dropped(self, enabled: None):
        chunks = [chunk(0)]
        client = DecisionClient([measured("answers", 0.99, others=("partially", "does_not_answer"))])

        _kept, report = await grade_answer_coverage("q", chunks, client=client)

        assert report["empty_guard_fired"] is False

    @pytest.mark.asyncio
    async def test_an_empty_input_is_handled(self, enabled: None):
        kept, report = await grade_answer_coverage("q", [], client=DecisionClient([]))

        assert kept == []
        assert report["empty_guard_fired"] is False

    @pytest.mark.asyncio
    async def test_a_blank_chunk_is_kept_without_being_graded(self, enabled: None):
        """There is nothing to send a model about, so there is nothing to judge."""
        chunks = [chunk(0, text="   "), chunk(1)]
        client = DecisionClient([measured("answers", 0.9, others=("partially", "does_not_answer"))])

        kept, report = await grade_answer_coverage("q", chunks, client=client)

        assert [c["id"] for c in kept] == ["c0", "c1"]
        assert report["graded"] == 1
        assert len(client.seen) == 1


class TestBudget:
    @pytest.mark.asyncio
    async def test_only_the_top_n_chunks_are_graded(self, enabled: None, monkeypatch: pytest.MonkeyPatch):
        """Grading all 20 candidates costs four times as much to change nothing.

        The chunks that would be dropped below the cut are ranked below the ones
        that survive anyway, so grading them is spend with no reachable effect.
        """
        monkeypatch.setattr(settings, "RAG_ANSWER_COVERAGE_TOP_N", 2)
        chunks = [chunk(index) for index in range(6)]
        client = DecisionClient([
            measured("answers", 0.9, others=("partially", "does_not_answer")) for _ in range(6)
        ])

        kept, report = await grade_answer_coverage("q", chunks, client=client)

        assert len(client.seen) == 2
        assert report["graded"] == 2
        assert len(kept) == 6

    @pytest.mark.asyncio
    async def test_a_zero_limit_disables_grading_without_touching_the_flag(self, enabled: None):
        chunks = [chunk(0)]
        client = DecisionClient([measured("does_not_answer", 0.99, others=("answers", "partially"))])

        kept, report = await grade_answer_coverage("q", chunks, client=client, top_n=0)

        assert kept == chunks
        assert client.seen == []

    @pytest.mark.asyncio
    async def test_only_the_passage_is_sent_not_the_whole_chunk(self, enabled: None):
        """A decision needs the claim being judged, not the entire parent chunk.

        The character cap is what stops a 200k-char artifact from being sent to a
        model on every query.
        """
        assert settings.RAG_ANSWER_COVERAGE_CHARS < 5000

        chunks = [chunk(0, text="x" * 100_000)]
        client = DecisionClient([measured("answers", 0.9, others=("partially", "does_not_answer"))])

        await grade_answer_coverage("q", chunks, client=client)

        # "Content:" occurs twice in the prompt: the decision layer wraps the state in
        # its own framing, and this stage's state itself opens with
        # "Question: ...\n\nContent:". Splitting on the first one therefore
        # recovers 22 characters of the *inner* framing and overstates the
        # passage by exactly that much -- which is how the first version of this
        # assertion failed at 1222 against a cap of 1200 with the code correct.
        # rsplit takes the innermost delimiter, which is the one that actually
        # bounds the text under test.
        _, passage = client.seen[0].rsplit("\n\nContent:\n", 1)
        passage = passage.split("\n\nReply with", 1)[0]

        assert len(passage) == settings.RAG_ANSWER_COVERAGE_CHARS, (
            "the cap should truncate to exactly CHARS characters"
        )
        assert len(passage) < 100_000, "the cap did not bound the passage at all"


class TestTheReport:
    @pytest.mark.asyncio
    async def test_every_decision_records_its_source(self, enabled: None, monkeypatch: pytest.MonkeyPatch):
        """A report that cannot distinguish a measured drop from a rule-based one
        cannot be used to evaluate anything -- which is the only reason to keep it.

        This is what would justify turning the flag on by default: compare the
        measured hits against whether the answer actually improved.

        The rule fallback is forced here by making the middle chunk's provider
        response carry no logprobs at all. That is the real shape of the failure
        -- the provider silently dropping the request under `drop_params=True` --
        so the fixture produces it rather than stubbing the decision layer, which
        would skip the part that actually has to work.
        """
        monkeypatch.setattr(settings, "RAG_ANSWER_COVERAGE_CHARS", 1200)
        chunks = [chunk(0), chunk(1), chunk(2)]
        client = DecisionClient([
            measured("answers", 0.9, others=("partially", "does_not_answer")),
            ("yes", None),  # provider returned no logprobs
            measured("does_not_answer", 0.9, others=("answers", "partially")),
        ])

        _kept, report = await grade_answer_coverage("q", chunks, client=client, deterministic=rule_no)

        assert report["measured"] == 2
        assert report["undecided"] == 0
        sources = [record["source"] for record in report["decisions"]]
        assert sources.count("logprobs") == 2
        assert sources.count("deterministic") == 1

    @pytest.mark.asyncio
    async def test_an_unmeasured_record_carries_no_probability_key(self, enabled: None):
        """Absent, not 0.0.

        A 0.0 would read as "measured, and it scored nothing", which is a
        different and much stronger claim than "not measured". A rule has to be
        supplied here or the decision is `None` and no record exists at all --
        which is what `test_every_decision_records_its_source` covers, so both
        shapes stay covered rather than one standing in for the other.
        """
        chunks = [chunk(0)]
        client = DecisionClient([ruled("partially")])

        _kept, report = await grade_answer_coverage("q", chunks, client=client, deterministic=rule_no)

        assert report["decisions"][0]["source"] == "deterministic"
        assert "probability" not in report["decisions"][0]
        assert "distribution" not in report["decisions"][0]

    @pytest.mark.asyncio
    async def test_a_measured_record_carries_its_distribution(self, enabled: None):
        chunks = [chunk(0)]
        client = DecisionClient([measured("answers", 0.75, others=("partially", "does_not_answer"))])

        _kept, report = await grade_answer_coverage("q", chunks, client=client)

        record = report["decisions"][0]
        assert record["probability"] == pytest.approx(0.75, abs=1e-4)
        assert sum(record["distribution"].values()) == pytest.approx(1.0, abs=1e-4)

    @pytest.mark.asyncio
    async def test_records_name_the_position_they_graded(self, enabled: None):
        """The empty-guard restore has to name exactly one record.

        Two chunks can carry identical text, so matching on content would restore
        an arbitrary one and leave the report lying about which survived.
        """
        chunks = [chunk(0, text="same"), chunk(1, text="same"), chunk(2, text="same")]
        client = DecisionClient([
            measured("does_not_answer", 0.99, others=("answers", "partially")) for _ in range(3)
        ])

        _kept, report = await grade_answer_coverage("q", chunks, client=client)

        assert [record["chunk_index"] for record in report["decisions"]] == [0, 1, 2]


class TestThePrompt:
    def test_the_question_names_the_failure_it_exists_to_catch(self):
        """Without the version clause the model answers a different question.

        "Is this about the same subject?" is what every other stage in the
        pipeline already asks, and the model would answer it correctly -- which
        is precisely the miss this stage was added for.
        """
        prompt = answer_coverage.COVERAGE_QUESTION.prompt.lower()
        assert "version" in prompt
        assert "does not" in prompt

    def test_partially_is_offered_because_a_binary_forces_a_wrong_answer(self):
        """The case that matters most is the one a yes/no question cannot express:
        a passage carrying half the answer would be scored as a total miss, or as
        a total hit, and both are wrong.
        """
        assert "partially" in answer_coverage.COVERAGE_LABELS
        assert len(answer_coverage.COVERAGE_LABELS) == 3

    def test_the_labels_are_a_closed_set_the_model_cannot_widen(self):
        from backend.app.services.decision import DecisionQuestion

        question = answer_coverage.COVERAGE_QUESTION
        assert isinstance(question, DecisionQuestion)
        with pytest.raises(ValueError):
            DecisionQuestion.choice("x", "y", ("answers", "answers"))
