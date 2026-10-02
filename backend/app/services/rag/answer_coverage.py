"""
Does this passage actually *answer* the question, or is it merely about it?

Every retrieval stage in this app ranks by similarity: hybrid dense+sparse
search fused with RRF, FlashRank reranking, MMR diversification. Similarity is
the right signal for "is this about the same subject", and it is blind to
version, edition, revision, and mode. A passage about the previous release of
something is highly similar to a question about the current one, scores well at
every stage, and is cited as though it answered the question.

That failure mode is not hypothetical for this codebase. `docs/architecture.md`
described the frontend as Next.js 14 while it was on 16.3.4, and the migration
graph carried two heads for a commit before `test_migration_graph.py` existed.
Nothing in the retrieval path would have caught either.

Grading "does this passage state the answer" is a closed-set judgement, not a
generation task, so it goes through the typed-decision layer and reads a real
distribution rather than asking a model how sure it is.

Two deliberate limits:

* **Only the top few chunks are graded.** Grading all `top_k` candidates costs
  four times as much to reach the same answer, because the chunks that would
  have been dropped are ranked below the ones that survive anyway.
* **Dropping requires a measurement.** A chunk is removed only when a real
  distribution put the winning label below `RAG_ANSWER_COVERAGE_DROP_BELOW`. A
  decision that fell back to a rule, or that could not be made at all, keeps its
  chunk. The failure direction is "cite something redundant" rather than "cite
  nothing and answer from nothing", and losing all evidence is the worse of the
  two errors.
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

import structlog
from backend.app.core.config import settings
from backend.app.services.decision import (
    SOURCE_LOGPROBS,
    Decision,
    DecisionQuestion,
    decide,
)

logger = structlog.get_logger(__name__)

# The closed answer space. Deliberately three labels rather than two: "partially"
# is the case that matters most and a binary forces a wrong choice between
# "answers" and "does not answer".
COVERAGE_LABELS = ("answers", "partially", "does_not_answer")

COVERAGE_QUESTION = DecisionQuestion(
    key="answer_coverage",
    prompt=(
        "Does the content state information that directly answers the question? "
        "Judge only what the content itself asserts. A passage on the right "
        "subject but the wrong version, edition, or configuration does not "
        "answer. If it contains some of the answer but not all, it partially "
        "answers."
    ),
    labels=COVERAGE_LABELS,
)

# A passage on the right subject at the wrong version is the failure this
# exists to catch, so the prompt has to say so. Without it the model reads
# "is this about the same thing?" and answers "answers", because that is the
# question every other stage in this pipeline is already asking.


def _chunk_text(chunk: dict[str, Any]) -> str:
    content = chunk.get("content") or chunk.get("text") or ""
    return str(content)[: settings.RAG_ANSWER_COVERAGE_CHARS]


async def grade_answer_coverage(
    query: str,
    chunks: list[dict[str, Any]],
    *,
    client: object | None = None,
    top_n: int | None = None,
    drop_below: float | None = None,
    deterministic: Callable[[str, DecisionQuestion], str | None] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Grade the leading chunks and drop the ones that cannot answer.

    Returns ``(kept_chunks, report)``. ``kept_chunks`` is the input list
    unchanged whenever grading is disabled, unavailable, or measures nothing --
    so a caller can always use the result and the feature flag does not have to
    be duplicated at every call site.

    ``report`` carries the per-chunk decisions for logging and for the ofline
    comparison that would justify turning this on by default. It records the
    source of every decision, because a report that cannot distinguish a
    measured drop from a rule-based one cannot be used to evaluate anything.
    """
    report: dict[str, Any] = {
        "enabled": False,
        "graded": 0,
        "dropped": 0,
        "measured": 0,
        # Graded chunks for which no honest decision existed at all. Distinct
        # from `graded - measured`, which counts chunks that fell back to a rule.
        "undecided": 0,
        "kept": len(chunks),
        # False until the guard below actually has to act, so a reader can tell
        # "nothing needed rescuing" from "the guard was not reached".
        "empty_guard_fired": False,
        "decisions": [],
    }

    if not settings.RAG_ANSWER_COVERAGE_ENABLED:
        return chunks, report
    if client is None or not chunks:
        return chunks, report

    limit = top_n if top_n is not None else settings.RAG_ANSWER_COVERAGE_TOP_N
    threshold = drop_below if drop_below is not None else settings.RAG_ANSWER_COVERAGE_DROP_BELOW
    if limit <= 0:
        return chunks, report

    # The tail is never graded, so it is never dropped -- only the head is
    # eligible for removal.
    head, tail = chunks[:limit], chunks[limit:]
    report["enabled"] = True

    kept: list[dict[str, Any]] = []
    for position, chunk in enumerate(head):
        text = _chunk_text(chunk)
        if not text.strip():
            kept.append(chunk)
            continue

        decision = await decide(
            f"Question: {query}\n\nContent:\n{text}",
            COVERAGE_QUESTION,
            client=client,
            deterministic=deterministic,
        )
        report["graded"] += 1
        if decision is None:
            # No honest answer available. Keeping the chunk is the safe
            # direction: an extra citation is recoverable, a missing one is not.
            #
            # Recorded rather than skipped, because "graded but unmeasured" and
            # "never graded" are different facts and a report that conflates them
            # cannot answer the only question it exists for -- whether the
            # provider is giving us real scores. Without this record, a
            # deployment where every decision failed would report a clean run.
            report["undecided"] += 1
            kept.append(chunk)
            continue

        record = _record(decision)
        # Positional, not content-based: two chunks can share identical text, and
        # the empty-guard restore below has to name exactly one of them.
        record["chunk_index"] = position
        report["decisions"].append(record)
        if decision.source == SOURCE_LOGPROBS:
            report["measured"] += 1
        dropped = _should_drop(decision, threshold)
        record["dropped"] = dropped
        if dropped:
            report["dropped"] += 1
        else:
            kept.append(chunk)

    result = kept + tail

    # Guard: never hand the synthesizer zero evidence. Every graded chunk coming
    # back a non-answer means the grader and the retriever disagree about this
    # question, which is a signal about the grader, not proof that the corpus
    # has nothing. Dropping the lot would silently convert a mis-calibrated
    # threshold into "I have no sources", and the model would then answer from
    # its own priors while the citations panel still shows an empty result --
    # the exact shape of an ungrounded answer this stage exists to prevent.
    # The top-ranked chunk is restored, and the report says so, so the anomaly
    # is visible rather than smoothed over.
    if chunks and not result:
        result = [head[0]]
        report["empty_guard_fired"] = True
        for record in report["decisions"]:
            if record.get("chunk_index") == 0:
                record["dropped"] = False
        report["dropped"] = max(0, report["dropped"] - 1)

    report["kept"] = len(result)
    logger.info(
        "rag_answer_coverage_graded",
        graded=report["graded"],
        dropped=report["dropped"],
        measured=report["measured"],
        kept=len(result),
        empty_guard=report["empty_guard_fired"],
    )
    return result, report


def _should_drop(decision: Decision, threshold: float) -> bool:
    """Drop only on a measurement, and only when the verdict is a clear non-answer.

    The single `probability is None` guard covers every unmeasured source: a
    rule-based decision and a failed model call both fall through to "keep",
    because `Decision.probability` only exists for a real distribution.
    """
    # One guard, not two. An earlier version checked `source` and then checked
    # `probability is None`; the second already covers every unmeasured source,
    # so the first was unreachable-and-unpinnable -- the revert harness reported
    # it as not load-bearing, correctly. Collapsing to a single condition means
    # there is one place to get wrong, and `Decision.probability` is where the
    # distinction between a measurement and a rule is already enforced.
    probability = decision.probability
    if probability is None:
        return False
    if decision.label == "does_not_answer":
        # A confident "no" is the clearest possible signal that this passage is
        # a distractor, so it does not need to clear the same bar as a weak yes.
        return True
    return decision.label == "partially" and probability < threshold


def _record(decision: Decision) -> dict[str, Any]:
    """Flatten a decision for the report, keeping the source and the nullability.

    ``probability`` stays absent rather than becoming 0.0 when unknown, so a
    report cannot be read as though an unmeasured chunk scored zero.
    """
    record: dict[str, Any] = {
        "key": decision.key,
        "label": decision.label,
        "source": decision.source,
    }
    if decision.has_distribution:
        record["probability"] = decision.probability
        record["distribution"] = decision.distribution
    return record
