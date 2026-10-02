"""
Typed decisions with honest probabilities.

A "decision" here is: given some state (text or JSON) and a question with a
*fixed set of allowed answers*, return which answer won -- and, where that is
knowable, how strongly.

Why this exists rather than a third-party "decision API". The whole mechanism is
two steps, and both are cheap:

1. Give the model a closed set of labels and ask for exactly one.
2. Read the model's per-token scores for those labels and normalise them.

Step 2 is the part that matters. Asking a chat model "how confident are you?"
gets a number it invented -- LLMs are famously bad at reporting their own
certainty, and the number reads exactly like a real one. Reading the actual
per-token scores does not have that failure mode, because there is nothing to
invent: the distribution is already there in the model, and asking a question
whose answer space is closed makes it meaningful.

So there are exactly two sources of truth here, and they are never mixed:

* ``SOURCE_LOGPROBS`` -- a real distribution read from the model. The only
  source that produces a probability.
* ``SOURCE_DETERMINISTIC`` -- a caller-supplied rule. It produces a *label* and
  deliberately produces **no probability**, because a rule has no confidence.

``Decision.probability`` is ``None`` whenever no real distribution exists. That
is the load-bearing part of the design: a caller that wants a number is forced
to handle its absence, so a heuristic can never be silently promoted into a
0.93. The deliberately absent third option -- "ask the model to rate its own
answer out of 10" -- is why this module returns ``None`` rather than guessing.

Not available, and returned as ``None`` rather than faked:

* providers that do not support logprobs, or that drop the request. LiteLLM is
  configured with ``drop_params=True``, so this is normal and not an error;
* a model whose top alternatives never contained any of the caller's labels,
  which means the constrained-answer framing did not take hold and the
  remaining scores are not a distribution over *this* answer space;
* fewer than two labels matched, because renormalising one survivor is just a
  confidence of 1.0 wearing a disguise.

The renormalisation is an approximation and is labelled as one. Only the tokens
that matched a label are kept and re-normalised, so probability mass the model
put on everything else is discarded. That is the standard constrained-decoding
approximation and it is accurate when the label set is what the model was
actually choosing between; it is not a guarantee, and callers that need one
should not treat the number as calibrated.

Three question shapes are supported, mirroring the useful part of the
vocabulary these APIs popularise:

* ``noul``   -- a yes/no judgement. Returns P(true).
* ``choice`` -- pick one of N options.
* ``score``  -- a grade on an ordered legend; ``graded_value`` turns the
  distribution into an expected value in 0..1.

No dependency is added for this. It runs on the LiteLLM router the app already
has, at whatever cheap model group is configured.
"""
from __future__ import annotations

import asyncio
import math
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import structlog
from backend.app.core.config import settings

logger = structlog.get_logger(__name__)

# The only two sources of truth. There is deliberately no third constant for
# "the model said it was confident" -- see the module docstring.
SOURCE_LOGPROBS = "logprobs"
SOURCE_DETERMINISTIC = "deterministic"

# A closed answer space plus "reply with one label and nothing else". The
# instruction to answer with a single word matters mechanically, not just
# stylistically: the distribution is read at the first generated token, so any
# preamble ("Based on the passage,") moves the answer position and the scores
# would describe the preamble instead.
_CLOSED_ANSWER_PROMPT = (
    "You answer questions from a closed set of options. Reply with exactly one "
    "option, copied verbatim, and nothing else. No punctuation, no explanation, "
    "no preamble."
)

_WHITESPACE = re.compile(r"\s+")
# Framing a model may add around a bare label: quotes, markdown emphasis, a
# leading list marker, trailing punctuation.
_LABEL_NOISE = re.compile(r"^[\s*_\-–—>#`\"'(\[{]+|[\s*_`\"'.,;:!?)\]}]+$")


@dataclass(frozen=True)
class DecisionQuestion:
    """A question with a closed answer space.

    ``labels`` is the contract: only these strings may ever be returned, and
    they are matched case-insensitively after stripping framing punctuation.
    Order is meaningful only for ``score``, where it defines the legend's scale.
    """

    key: str
    prompt: str
    labels: tuple[str, ...]

    def __post_init__(self) -> None:
        if len(self.labels) < 2:
            raise ValueError(
                f"decision question {self.key!r} needs at least 2 labels; a single "
                "label is a constant, not a decision"
            )
        normalised = [_normalise_label(label) for label in self.labels]
        if len(set(normalised)) != len(normalised):
            raise ValueError(
                f"decision question {self.key!r} has labels that collide once "
                f"normalised: {self.labels!r}"
            )

    @classmethod
    def noul(cls, key: str, prompt: str) -> DecisionQuestion:
        """A yes/no judgement. The winning probability is P(true)."""
        return cls(key=key, prompt=prompt, labels=("yes", "no"))

    @classmethod
    def choice(cls, key: str, prompt: str, labels: tuple[str, ...] | list[str]) -> DecisionQuestion:
        """Pick one of N options."""
        return cls(key=key, prompt=prompt, labels=tuple(labels))

    @classmethod
    def score(cls, key: str, prompt: str, legend: tuple[str, ...] | list[str]) -> DecisionQuestion:
        """Grade on an ordered legend, lowest to highest."""
        return cls(key=key, prompt=prompt, labels=tuple(legend))


@dataclass(frozen=True)
class Decision:
    """The outcome of one question.

    ``probability`` is ``None`` unless a real distribution was read. Callers
    that want a number must therefore handle its absence -- which is the entire
    reason this class exists rather than a bare ``(label, score)`` tuple.
    """

    key: str
    label: str
    source: str
    distribution: dict[str, float] = field(default_factory=dict)

    @property
    def has_distribution(self) -> bool:
        return self.source == SOURCE_LOGPROBS and bool(self.distribution)

    @property
    def probability(self) -> float | None:
        """Mass on the winning label, or None when nothing was measured.

        None means "unknown", never "zero" and never "one". A caller that
        substitutes a default here is how a heuristic becomes a 0.93.
        """
        if not self.has_distribution:
            return None
        return self.distribution.get(self.label)

    def graded_value(self, legend: tuple[str, ...] | list[str]) -> float | None:
        """Expected value over an ordered legend, in 0..1.

        None when there is no distribution. The first legend entry scores 0.0
        and the last 1.0.
        """
        if not self.has_distribution:
            return None
        ordered = list(legend)
        if len(ordered) < 2:
            return None
        span = len(ordered) - 1
        total = 0.0
        for index, label in enumerate(ordered):
            total += self.distribution.get(label, 0.0) * (index / span)
        return round(total, 4)


def _normalise_label(label: str) -> str:
    """Case-fold a label and strip framing so model output can be matched to it."""
    return _WHITESPACE.sub(" ", _LABEL_NOISE.sub("", str(label).strip())).strip().lower()


def _score_labels(
    question: DecisionQuestion,
    tokens: list[dict[str, Any]],
) -> dict[str, float] | None:
    """Turn per-token logprobs into a distribution over the question's labels.

    Returns None when the answer space is not recoverable from the scores --
    which is a real, common outcome and never means "confident".
    """
    wanted = {_normalise_label(label): label for label in question.labels}

    # The answer position is the first token that carries alternatives at all.
    # A leading space or newline is common and carries nothing useful, and
    # taking the first token unconditionally would read the distribution for
    # whatever the model emitted before it committed to an answer.
    entry = next((t for t in tokens if t.get("top")), None)
    if entry is None:
        return None

    best: dict[str, float] = {}
    for token, logprob in entry["top"]:
        candidate = _normalise_label(token)
        if candidate in wanted:
            label = wanted[candidate]
            # Several tokens can normalise onto one label ("yes", "Yes", " YES").
            # Keep the most likely, which is the only defensible collapse.
            if label not in best or logprob > best[label]:
                best[label] = logprob
            continue
        # Tokenizer boundary mismatch: the model may have split or padded the
        # label ("Answer", " partially"). Accept the longest label the token
        # starts with, since a longer prefix is a more specific match than a
        # shorter one that also happens to fit.
        for normalised, label in wanted.items():
            if normalised and normalised.startswith(candidate) and len(candidate) >= 2:
                if label not in best or logprob > best[label]:
                    best[label] = logprob
                break

    if len(best) < 2:
        # One survivor renormalises to 1.0, which would be a fabricated
        # certainty rather than a measurement.
        return None

    peak = max(best.values())
    weights = {label: math.exp(value - peak) for label, value in best.items()}
    total = sum(weights.values())
    if total <= 0 or not math.isfinite(total):
        return None
    return {label: round(weight / total, 6) for label, weight in weights.items()}


async def decide(
    state: str,
    question: DecisionQuestion,
    *,
    client: object | None = None,
    model: str | None = None,
    deterministic: Callable[[str, DecisionQuestion], str | None] | None = None,
    prefer_deterministic: bool = False,
    timeout_seconds: float | None = None,
    top_logprobs: int | None = None,
) -> Decision | None:
    """Answer one closed-set question about ``state``.

    Returns a ``Decision``, or ``None`` when no honest answer exists -- no
    client, a provider without logprobs, a timeout, an error, unusable output,
    or no rule to fall back on. ``None`` means "no information" and callers must
    not read it as "no".

    ``deterministic`` is a fallback, not a peer: it runs only when the model
    path cannot produce a measurement, and its result is labelled
    ``SOURCE_DETERMINISTIC`` with no probability. Pass
    ``prefer_deterministic=True`` to run the rule first and spend no model call
    when it is confident enough to answer on its own.

    Fail-open in one direction only: any failure here degrades to the caller's
    rule, and failing that to ``None``. A decision layer that could make a
    request fail would be a worse failure than having no decision layer.
    """
    if prefer_deterministic:
        ruled = _from_rule(state, question, deterministic)
        if ruled is not None:
            return ruled

    if client is None:
        return _from_rule(state, question, deterministic)

    timeout = timeout_seconds if timeout_seconds is not None else settings.DECISION_TIMEOUT_SECONDS
    resolved_model = model or settings.DECISION_MODEL
    window = top_logprobs if top_logprobs is not None else settings.DECISION_TOP_LOGPROBS

    messages = [
        {"role": "system", "content": _CLOSED_ANSWER_PROMPT},
        {"role": "user", "content": (
            f"Options: {', '.join(question.labels)}\n\n"
            f"Question: {question.prompt}\n\n"
            f"Content:\n{state}\n\n"
            "Reply with one option, exactly as written above."
        )},
    ]

    try:
        result = await asyncio.wait_for(
            _request(client, messages, resolved_model, window),
            timeout=timeout,
        )
    except asyncio.TimeoutError:
        logger.warning("decision_timeout", key=question.key, model=resolved_model, timeout_s=timeout)
        return _from_rule(state, question, deterministic)
    except asyncio.CancelledError:
        # A client disconnect or a shutdown. Propagating is the only correct
        # behaviour; swallowing it would defeat cancellation.
        raise
    except Exception as exc:
        logger.warning(
            "decision_failed",
            key=question.key,
            model=resolved_model,
            error_type=type(exc).__name__,
            error=str(exc)[:200],
        )
        return _from_rule(state, question, deterministic)

    if not isinstance(result, dict):
        return _from_rule(state, question, deterministic)

    tokens = result.get("logprobs")
    if not tokens:
        # Not an error: providers drop this request routinely, and `drop_params`
        # makes it silent. Logged so a deployment that believes it is getting
        # real probabilities finds out that it is not.
        logger.info("decision_logprobs_unavailable", key=question.key, model=resolved_model)
        return _from_rule(state, question, deterministic)

    distribution = _score_labels(question, tokens)
    if distribution is None:
        logger.info(
            "decision_distribution_unrecoverable",
            key=question.key,
            model=resolved_model,
            labels=list(question.labels),
        )
        return _from_rule(state, question, deterministic)

    winner = max(distribution.items(), key=lambda item: (item[1], item[0]))
    decision = Decision(
        key=question.key,
        label=winner[0],
        source=SOURCE_LOGPROBS,
        distribution=distribution,
    )
    logger.info(
        "decision_logprobs",
        key=question.key,
        model=resolved_model,
        label=winner[0],
        probability=decision.probability,
        matched=len(distribution),
        of=len(question.labels),
    )
    return decision


async def _request(
    client: object,
    messages: list[dict[str, str]],
    model: str,
    top_logprobs: int,
) -> dict[str, Any]:
    """One call through ``complete()``, never the router and never ``completion()``.

    ``complete()`` is the only path that charges the per-run spend meter
    (``litellm_client.py``), so going around it would make this free to write
    and invisible to the ceiling in ``core/spend.py``. It is also the only path
    that returns ``logprobs``.
    """
    complete = getattr(client, "complete", None)
    if complete is None:
        raise TypeError("injected decision client exposes no complete()")
    result = await complete(
        messages=messages,
        model=model,
        temperature=0.0,
        # Enough for the label plus a little slack, and no more: the answer is
        # read at the first token, so any extra generation is paid for and
        # never read.
        max_tokens=settings.DECISION_MAX_TOKENS,
        logprobs=True,
        top_logprobs=top_logprobs,
    )
    if not isinstance(result, dict):
        raise TypeError("complete() returned a non-dict result")
    return result


def _from_rule(
    state: str,
    question: DecisionQuestion,
    deterministic: Callable[[str, DecisionQuestion], str | None] | None,
) -> Decision | None:
    """Run the caller's rule, if any, and label it as unmeasured.

    A rule that raises, returns something outside the label set, or returns
    nothing yields ``None`` rather than a guess. Guessing here would be the one
    place a fabricated answer could enter the system, since it is the path taken
    precisely when the measured path has failed.
    """
    if deterministic is None:
        return None
    try:
        label = deterministic(state, question)
    except Exception as exc:
        logger.warning(
            "decision_rule_failed",
            key=question.key,
            error_type=type(exc).__name__,
        )
        return None
    if label is None:
        return None

    wanted = {_normalise_label(item): item for item in question.labels}
    resolved = wanted.get(_normalise_label(label))
    if resolved is None:
        logger.warning(
            "decision_rule_returned_unknown_label",
            key=question.key,
            returned=str(label)[:60],
            labels=list(question.labels),
        )
        return None

    return Decision(key=question.key, label=resolved, source=SOURCE_DETERMINISTIC)
