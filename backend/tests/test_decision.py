"""Tests for the typed-decision layer (services/decision.py).

The whole point of this module is that a probability means something specific:
it came from a real distribution read off the model, not from a number the model
made up and not from a rule that happens to be confident. Most of these tests
exist to pin that distinction, because the failure it prevents -- a heuristic
laundered into a calibrated-looking 0.93 -- produces output that looks correct
and is not.
"""
from __future__ import annotations

import asyncio
import math
from typing import Any

import pytest
from backend.app.core.config import settings
from backend.app.services.decision import (
    SOURCE_DETERMINISTIC,
    SOURCE_LOGPROBS,
    Decision,
    DecisionQuestion,
    _normalise_label,
    _score_labels,
    decide,
)

YES_NO = DecisionQuestion.noul("is_relevant", "Is this relevant?")
CHOICE = DecisionQuestion.choice("lang", "Which language?", ("python", "rust", "go"))
LEGEND = ("none", "some", "most", "all")


def tokens(*entries: tuple[str, list[tuple[str, float]]]) -> list[dict[str, Any]]:
    """Build a logprobs payload shaped the way `_extract_logprobs` emits one."""
    return [
        {"token": name, "logprob": -0.01, "top": list(alts)}
        for name, alts in entries
    ]


def flat(payload: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Assert the fixture really is flat, so tests cannot pass on a lying fake."""
    assert all(
        set(entry) == {"token", "logprob", "top"} for entry in payload
    ), "fixture drifted from the _extract_logprobs shape"
    return payload


class FakeClient:
    """Records every call so tests can assert what was asked and how."""

    def __init__(self, payload: list[dict[str, Any]] | None, *, error: Exception | None = None):
        self.payload = payload
        self.error = error
        self.calls: list[dict[str, Any]] = []

    async def complete(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return {"content": "yes", "logprobs": self.payload}


def yes_no_payload(yes_logprob: float = -0.05, no_logprob: float = -3.0):
    """A first token whose alternatives are exactly `yes` and `no`.

    The probabilities are asserted downstream, so a change to how the fixture
    builds them is caught rather than silently shifting every expectation.
    """
    payload = tokens(("yes", [(" yes", yes_logprob), (" no", no_logprob)]))
    return flat(payload)


class TestClosedSetMatching:
    def test_only_labels_are_returned_even_when_the_model_emits_something_else(self):
        """The provider may hand back a completely different distribution.

        The answer space is the caller's, not the model's: " I think yes" and
        "Sure! Based on the passage, yes" must still resolve to `yes`, and a
        distribution with no label in it at all must resolve to nothing rather
        than to whichever token happened to score highest.
        """
        usable = _score_labels(YES_NO, yes_no_payload())
        assert usable is not None
        assert set(usable) == {"yes", "no"}

        assert _score_labels(YES_NO, flat(tokens(("yes", [(" I think", -0.1)])))) is None

    def test_the_answer_position_is_the_first_token_carrying_alternatives(self):
        """Leading whitespace is the norm, not an edge case.

        Providers prepend a space or newline before the committed token. Taking
        token 0 unconditionally reads the distribution for whatever the model
        emitted before it answered -- which is noise, not a decision.
        """
        payload = flat(
            tokens(
                ("", []),  # a bare whitespace token: no alternatives to read
                (" yes", [(" yes", -0.10), (" no", -2.30)]),
            )
        )
        distribution = _score_labels(YES_NO, payload)
        assert distribution is not None
        assert max(distribution, key=lambda label: distribution[label]) == "yes"

    def test_a_leading_alternative_without_a_label_does_not_win_by_default(self):
        """An unlabelled leader must not be laundered into the label set."""
        payload = flat(tokens(("x", [(" yes", -3.0), (" no", -3.0), (" x", -0.01)])))
        distribution = _score_labels(YES_NO, payload)
        assert distribution is not None
        assert "x" not in distribution
        assert set(distribution) == {"yes", "no"}

    @pytest.mark.parametrize(
        ("token", "label"),
        [
            ("yes", "yes"),
            (" Yes", "yes"),
            ("YES", "yes"),
            ("**yes**", "yes"),
            ("`yes`", "yes"),
            ('"no"', "no"),
            ("- no", "no"),
            ("  No.  ", "no"),
        ],
    )
    def test_model_framing_around_a_label_still_matches(self, token: str, label: str):
        """Models wrap bare answers in quotes, bullets and emphasis.

        If normalisation did not strip that framing, every such answer would read
        as "no label found" and the layer would silently degrade to its rule --
        which is the kind of degradation that looks like a provider that does
        not support logprobs.

        Both labels have to be present in the payload: with one survivor
        `_score_labels` refuses to renormalise, so a fixture offering only the
        framed label would pass for the wrong reason (it would look like the
        framing was unmatched when in fact there was nothing to compare against).
        """
        assert _normalise_label(token) == label
        other = "no" if label == "yes" else "yes"
        payload = flat(tokens((token, [(token, -0.10), (other, -3.0)])))
        distribution = _score_labels(YES_NO, payload)
        assert distribution is not None
        assert set(distribution) == {"yes", "no"}
        assert max(distribution, key=lambda key: distribution[key]) == label

    def test_the_longest_matching_label_wins_a_tokenizer_split(self):
        """A provider may split a label across tokens ("does", "_not", "answer").

        Matching only on exact equality loses those; matching on "starts with"
        without preferring the longest match would let a short label swallow a
        longer one that also fits.
        """
        question = DecisionQuestion.choice(
            "shape", "What shape?", ("does_not_answer", "answers")
        )
        payload = flat(tokens(("does", [(" does", -0.20), (" answers", -2.00)])))
        distribution = _score_labels(question, payload)
        assert distribution is not None
        assert max(distribution, key=lambda key: distribution[key]) == "does_not_answer"

    def test_a_single_survivor_is_refused_because_it_is_not_a_distribution(self):
        """One matching label renormalises to 1.0, which is a certainty not a measure.

        Accepting it would mean a provider whose top-20 happened to contain only
        `yes` produces a confident 1.0 -- the exact fabricated probability this
        module exists to prevent.
        """
        payload = flat(tokens(("yes", [(" yes", -0.10), (" The", -3.0)])))
        assert _score_labels(YES_NO, payload) is None

    def test_case_differing_labels_are_rejected_at_construction(self):
        """Two labels that normalise alike would make the answer space ambiguous."""
        with pytest.raises(ValueError, match="normalised"):
            DecisionQuestion.choice("bad", "q", ("Yes", "yes"))

    def test_a_single_label_question_is_a_constant_not_a_decision(self):
        with pytest.raises(ValueError, match="at least 2 labels"):
            DecisionQuestion.choice("bad", "q", ("only",))


class TestHonestProbabilities:
    @pytest.mark.asyncio
    async def test_a_measured_decision_reports_the_mass_on_the_winning_label(self):
        client = FakeClient(yes_no_payload(yes_logprob=-0.05, no_logprob=-3.0))
        decision = await decide("a passage about rust", YES_NO, client=client)

        assert decision is not None
        assert decision.source == SOURCE_LOGPROBS
        assert decision.label == "yes"
        assert decision.has_distribution
        assert decision.probability is not None
        # -exp(-0.05) vs -exp(-3.0) renormalised: the mass belongs to `yes`.
        expected = math.exp(-0.05) / (math.exp(-0.05) + math.exp(-3.0))
        assert decision.probability == pytest.approx(expected, rel=1e-4)
        assert sum(decision.distribution.values()) == pytest.approx(1.0, abs=1e-4)

    @pytest.mark.asyncio
    async def test_a_rule_decision_has_no_probability_at_all(self):
        """The single most important invariant in the module.

        A deterministic rule has no confidence. Reporting one -- even 1.0 -- makes
        the caller unable to tell a measurement from a guess, which is the whole
        failure this design was built to make impossible.
        """
        decision = await decide(
            "some state",
            YES_NO,
            client=None,
            deterministic=lambda state, question: "yes",
        )
        assert decision is not None
        assert decision.source == SOURCE_DETERMINISTIC
        assert decision.label == "yes"
        assert decision.probability is None
        assert not decision.has_distribution
        assert decision.distribution == {}

    @pytest.mark.asyncio
    async def test_a_provider_that_returns_no_logprobs_yields_none_not_a_certainty(self):
        """LiteLLM is configured with drop_params=True, so this is the normal path.

        A layer that answered "yes" with no measurement would make the pipeline
        behave as though the grader worked on every provider that cannot supply
        scores -- i.e. silently everywhere.
        """
        client = FakeClient(payload=None)
        decision = await decide("state", YES_NO, client=client)

        assert decision is None
        assert client.calls, "the model should still have been asked"

    @pytest.mark.asyncio
    async def test_unrecoverable_scores_fall_back_to_the_rule_and_still_claim_nothing(self):
        client = FakeClient(payload=flat(tokens(("yes", [(" I think so", -0.1)]))))
        decision = await decide("state", YES_NO, client=client, deterministic=lambda s, q: "no")

        assert decision is not None
        assert decision.source == SOURCE_DETERMINISTIC
        assert decision.label == "no"
        assert decision.probability is None

    @pytest.mark.asyncio
    async def test_graded_value_is_none_without_a_distribution(self):
        decision = await decide("s", DecisionQuestion.score("g", "q", LEGEND), client=None,
                                deterministic=lambda s, q: "some")
        assert decision is not None
        assert decision.graded_value(LEGEND) is None

    @pytest.mark.asyncio
    async def test_graded_value_is_the_expected_position_on_the_legend(self):
        """A grade is an expectation over the legend, not the argmax's position.

        Reading only the winning label throws away the spread, which is most of
        what a graded judgement is for: a near-tie between `some` and `most` is a
        meaningfully different fact from a clean `some`.
        """
        # Mass split across the middle two legend entries, with the extremes
        # nearly ruled out. The winner is `some` at ~0.51 -- only just ahead.
        client = FakeClient(flat(tokens(("x", [(" none", -4.0), (" some", -0.20), (" most", -0.30), (" all", -4.0)]))))
        question = DecisionQuestion.score("g", "How much does it cover?", LEGEND)
        decision = await decide("state", question, client=client)

        assert decision is not None
        assert decision.label == "some"
        graded = decision.graded_value(LEGEND)
        assert graded is not None

        weights = {label: math.exp(value) for label, value in
                   (("none", -4.0), ("some", -0.20), ("most", -0.30), ("all", -4.0))}
        total = sum(weights.values())
        positions = {"none": 0.0, "some": 1 / 3, "most": 2 / 3, "all": 1.0}
        expected = sum(positions[label] * value / total for label, value in weights.items())
        assert graded == pytest.approx(expected, abs=1e-4)

        # The assertion that makes this test worth having: the expectation is
        # pulled most of the way to `most` by the mass sitting there, so it is
        # near the middle of the legend rather than at the winner's own position.
        # An implementation that just returned the argmax's index -- the obvious
        # shortcut -- returns 1/3 and fails both of these.
        assert graded > 0.45, f"expected the mass on `most` to pull the value up, got {graded}"
        assert abs(graded - positions[decision.label]) > 0.10

    def test_graded_value_needs_at_least_two_legend_entries(self):
        decision = Decision("k", "a", SOURCE_LOGPROBS, {"a": 1.0})
        assert decision.graded_value(("a",)) is None


class TestFailOpen:
    @pytest.mark.asyncio
    async def test_a_provider_error_falls_back_to_the_rule(self):
        client = FakeClient(payload=None, error=RuntimeError("provider down"))
        decision = await decide("state", YES_NO, client=client, deterministic=lambda s, q: "no")

        assert decision is not None
        assert decision.source == SOURCE_DETERMINISTIC
        assert decision.label == "no"

    @pytest.mark.asyncio
    async def test_a_provider_error_with_no_rule_returns_none_rather_than_a_default(self):
        """No client, no rule, no measurement must be silence, not a coin flip.

        Defaulting to `yes` or `no` would make an outage look like a verdict.
        """
        client = FakeClient(payload=None, error=RuntimeError("provider down"))
        assert await decide("state", YES_NO, client=client) is None

    @pytest.mark.asyncio
    async def test_a_timeout_falls_back_to_the_rule(self):
        class Slow:
            async def complete(self, **kwargs: Any) -> dict[str, Any]:
                await asyncio.sleep(10)
                return {"content": "yes", "logprobs": []}

        decision = await decide(
            "state", YES_NO, client=Slow(), timeout_seconds=0.05,
            deterministic=lambda s, q: "yes",
        )
        assert decision is not None
        assert decision.source == SOURCE_DETERMINISTIC

    @pytest.mark.asyncio
    async def test_cancellation_propagates(self):
        """A client disconnect or shutdown must not be converted into a decision.

        `CancelledError` inherits BaseException precisely so a broad handler cannot
        eat it. Swallowing it here would defeat the cancellation of the whole
        request and, worse, would report a fabricated label to a caller that has
        already gone away.
        """

        class Cancelling:
            async def complete(self, **kwargs: Any) -> dict[str, Any]:
                raise asyncio.CancelledError()

        with pytest.raises(asyncio.CancelledError):
            await decide("state", YES_NO, client=Cancelling(), deterministic=lambda s, q: "yes")

    @pytest.mark.asyncio
    async def test_a_rule_that_raises_yields_none_not_a_guess(self):
        """This is the path taken exactly when the measured path failed, so it is
        the last place a fabricated label could enter. Guessing here would be
        worse than having no decision at all."""

        def rule(state: str, question: DecisionQuestion) -> str:
            raise ValueError("bad rule")

        client = FakeClient(payload=None, error=RuntimeError("down"))
        assert await decide("state", YES_NO, client=client, deterministic=rule) is None

    @pytest.mark.asyncio
    async def test_a_rule_returning_an_unknown_label_is_refused(self):
        """A rule that answers outside the closed set is a bug in the rule.

        Passing it through would put a label in the report that nothing can
        interpret, and downstream thresholds compare against known labels.
        """
        client = FakeClient(payload=None, error=RuntimeError("down"))
        decision = await decide("state", YES_NO, client=client, deterministic=lambda s, q: "maybe")
        assert decision is None

    @pytest.mark.asyncio
    async def test_prefer_deterministic_skips_the_model_call_entirely(self):
        """The cheap path must actually be free.

        If the rule ran first but the model call still went out, turning the
        model off would save nothing, and this flag would be decorative.
        """
        client = FakeClient(yes_no_payload())
        decision = await decide(
            "state", YES_NO, client=client, prefer_deterministic=True,
            deterministic=lambda s, q: "no",
        )
        assert decision is not None
        assert decision.source == SOURCE_DETERMINISTIC
        assert client.calls == []


class TestTheRequestItself:
    @pytest.mark.asyncio
    async def test_the_call_goes_through_complete_so_the_spend_meter_sees_it(self):
        """`complete()` is the only path that charges `core/spend.py`.

        Reaching past it to `completion()` or to the router would produce a
        grader that costs money and is invisible to the per-run ceiling -- the
        same trap HyDE hit. Asserted on the callee name, not on the result.
        """
        client = FakeClient(yes_no_payload())
        await decide("state", YES_NO, client=client)

        assert len(client.calls) == 1
        assert not hasattr(client, "completion")

    @pytest.mark.asyncio
    async def test_the_call_asks_for_logprobs_at_zero_temperature(self):
        """Three separate things break if these are wrong.

        Without `logprobs=True` the provider returns nothing and the layer
        degrades to its rule everywhere. Temperature above 0 makes the
        distribution a sample rather than the model's actual preference, which
        is the one thing this module is reading.
        """
        client = FakeClient(yes_no_payload())
        await decide("state", YES_NO, client=client)

        call = client.calls[0]
        assert call["logprobs"] is True
        assert call["temperature"] == 0.0
        assert call["top_logprobs"] == settings.DECISION_TOP_LOGPROBS

    @pytest.mark.asyncio
    async def test_generation_is_capped_because_only_the_first_token_is_read(self):
        """The distribution is read at the answer token. Everything after it is
        charged for and never inspected, so an unbounded budget here is pure
        waste on the query path."""
        client = FakeClient(yes_no_payload())
        await decide("state", YES_NO, client=client)

        assert client.calls[0]["max_tokens"] == settings.DECISION_MAX_TOKENS
        assert settings.DECISION_MAX_TOKENS <= 16

    @pytest.mark.asyncio
    async def test_the_labels_and_the_question_reach_the_model(self):
        """The answer space has to be stated, or the model answers in its own
        words and no token matches a label."""
        client = FakeClient(yes_no_payload())
        await decide("a long passage", YES_NO, client=client)

        user = client.calls[0]["messages"][-1]["content"]
        assert "yes" in user and "no" in user
        assert "Is this relevant?" in user
        assert "a long passage" in user
