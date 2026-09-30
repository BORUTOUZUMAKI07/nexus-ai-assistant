"""
Per-run spend ceiling for the agent graph.

The problem
-----------

Every loop in the graph is individually bounded, and their *product* is not.
One turn can spend:

* ``CRITIC_MAX_REVISIONS`` draft+critic pairs, each a full-strength model call
* ``CRAG_MAX_REVISIONS`` corrective re-queries
* the researcher's own decomposition, reflection and synthesis calls
* subagent dispatches, several of which run concurrently

Each ceiling is a sane number. Composed, they multiply. Nothing in the codebase
tracks cumulative spend for a run, so no single place can notice that a turn
has gone long -- and by the time a user sees a runaway turn, the cost has
already been incurred.

Notably, across the five reference repositories surveyed for this work, none
bounds an agent loop's spend either. gpt-researcher tracks cost per step
(``agent.py:785-800``) and reports it, but never refuses to take the next
step. This is a gap worth closing rather than a gap to catch up on.

Design
------

**Charged automatically, at the one place that knows.** ``litellm_client``
already computes ``tokens_input``/``tokens_output`` for every call it makes, so
the meter is fed there. Every model call in the process is therefore counted
without touching a single call site, including calls added later. A node that
forgets to account for itself is exactly the failure mode a hand-maintained
counter would have.

**Durable across suspension.** The meter lives in a contextvar for the normal
case, but the graph can suspend for HITL approval and resume in a later
request, where contextvars are gone. ``AgentState`` therefore also carries
``loop_steps`` and ``tokens_used``, and a resumed run is seeded from them.
In-memory accounting that vanishes on suspend would be an accounting system
that misses precisely the long runs it exists to catch.

**Fails open on absence, not on breakage.** No meter bound means no ceiling,
which is the direction that matters: anything that forgot to bind a meter runs
unbounded rather than refusing every request. But a *broken* meter raises
rather than being swallowed, because the only realistic cause is a bug in the
meter itself, and a silent swallow would hide that bug from the test that
introduced it. Bad *values* are handled inside the meter, which coerces them
to no-ops.
"""
from __future__ import annotations

import threading
from contextvars import ContextVar, Token
from dataclasses import dataclass, field
from typing import Any

#: Estimated cost of a step, used by `can_afford` before the work is done.
#: Deliberately pessimistic: a step that would overrun is one we skip.
DEFAULT_STEP_TOKEN_ESTIMATE = 2000


@dataclass
class SpendMeter:
    """Cumulative accounting for one agent run.

    Cheap to construct and to touch, because it sits on the request path of
    every model call. Counters are plain ints; the guard is a comparison.
    """

    limit_tokens: int = 60_000
    limit_steps: int = 8
    tokens: int = 0
    steps: int = 0
    calls: int = 0
    #: Tokens the run had already spent when it was seeded from a resumed state.
    seeded_tokens: int = 0
    seeded_steps: int = 0
    #: Why the ceiling first stopped the run, for the log line and the client.
    stop_reason: str = ""
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    # -- charging ------------------------------------------------------------

    def charge_tokens(self, tokens: int) -> None:
        """Record tokens spent by a completed model call.

        Ignores non-positive and non-numeric values: usage accounting must
        never be the thing that breaks a generation.
        """
        try:
            amount = int(tokens)
        except (TypeError, ValueError):
            return
        if amount <= 0:
            return
        with self._lock:
            self.tokens += amount
            self.calls += 1

    def charge_step(self) -> None:
        """Record one unit of agent work (a revision, a re-query, a dispatch)."""
        with self._lock:
            self.steps += 1

    # -- querying ------------------------------------------------------------

    @property
    def tokens_remaining(self) -> int:
        return max(0, self.limit_tokens - self.tokens)

    @property
    def steps_remaining(self) -> int:
        return max(0, self.limit_steps - self.steps)

    @property
    def tokens_exhausted(self) -> bool:
        return self.tokens >= self.limit_tokens

    @property
    def steps_exhausted(self) -> bool:
        return self.steps >= self.limit_steps

    @property
    def exhausted(self) -> bool:
        return self.tokens_exhausted or self.steps_exhausted

    def can_afford(self, estimated_tokens: int = DEFAULT_STEP_TOKEN_ESTIMATE) -> bool:
        """Whether a step of roughly this size still fits.

        Checked *before* the work, not after: a ceiling that only notices
        afterwards has already paid for the thing it was meant to prevent.
        """
        if self.exhausted:
            return False
        try:
            estimate = max(0, int(estimated_tokens))
        except (TypeError, ValueError):
            estimate = DEFAULT_STEP_TOKEN_ESTIMATE
        return self.tokens + estimate <= self.limit_tokens

    def stop(self, reason: str) -> None:
        """Record why the run was cut short. First reason wins.

        First-wins because the first ceiling to bite is the one that explains
        the behaviour; a later, secondary stop would be a consequence.
        """
        if not self.stop_reason:
            self.stop_reason = reason

    def snapshot(self) -> dict[str, Any]:
        """Log/serialisable view. Carries no secrets and no prompt content."""
        return {
            "tokens": self.tokens,
            "steps": self.steps,
            "calls": self.calls,
            "limit_tokens": self.limit_tokens,
            "limit_steps": self.limit_steps,
            "tokens_remaining": self.tokens_remaining,
            "steps_remaining": self.steps_remaining,
            "exhausted": self.exhausted,
            "stop_reason": self.stop_reason,
        }

    def merge_from(self, tokens: int, steps: int) -> None:
        """Adopt counters carried across a suspend/resume boundary.

        Additive, so a resumed run cannot reset its own budget by resuming
        twice.
        """
        with self._lock:
            for name, value in (("tokens", tokens), ("steps", steps)):
                try:
                    amount = int(value)
                except (TypeError, ValueError):
                    continue
                if amount > getattr(self, name):
                    setattr(self, name, amount)
                    setattr(self, f"seeded_{name}", amount)


_meter: ContextVar[SpendMeter | None] = ContextVar("nexus_spend_meter", default=None)


def bind_spend_meter(
    *,
    limit_tokens: int,
    limit_steps: int,
    seed_tokens: int = 0,
    seed_steps: int = 0,
) -> Token[SpendMeter | None]:
    """Bind a meter to the current context and return the reset token.

    Seeding matters for HITL resume: the contextvar is gone by then, so the
    run's real spend has to arrive through the checkpointed state.
    """
    meter = SpendMeter(limit_tokens=int(limit_tokens), limit_steps=int(limit_steps))
    meter.merge_from(seed_tokens, seed_steps)
    return _meter.set(meter)

def reset_spend_meter(token: Token[SpendMeter | None]) -> None:
    """Restore the previous meter.

    Must be called in a ``finally``. Contextvars outlive the statement that
    set them, so a missing reset leaks one run's budget into the next request
    served by the same task -- which would silently disable the ceiling.

    The catch is deliberately wide. ``ContextVar.reset`` raises ``ValueError``
    for a token from another context, but a token of the wrong *type* raises
    ``TypeError``, and an unrecognised token object is not a reason to leave a
    spent meter in place: dropping it restores the unbounded default, which is
    the safe direction, whereas keeping it would keep refusing work.
    """
    try:
        _meter.reset(token)
    except Exception:
        _meter.set(None)


def current_spend_meter() -> SpendMeter | None:
    """The active meter, or None when nothing is bounding this run."""
    return _meter.get()


def clear_spend_meter() -> None:
    _meter.set(None)


def charge_tokens(tokens: int) -> None:
    """Module-level convenience for the model client. No meter, no charge.

    Deliberately not wrapped in try/except. The meter is on the hot path of
    every model call in the process, and the failure it could be hiding -- a
    `SpendMeter` that raises -- is a bug in the meter, not bad input from the
    model client. `SpendMeter.charge_tokens` already coerces a non-numeric or
    negative count to a no-op, which is the fail-open that matters here; an
    outer swallow would only make a broken meter invisible in the very test
    that broke it.
    """
    meter = _meter.get()
    if meter is not None:
        meter.charge_tokens(tokens)


def charge_step() -> None:
    """Module-level convenience for loop bodies. See `charge_tokens`."""
    meter = _meter.get()
    if meter is not None:
        meter.charge_step()


def budget_exhausted(
    estimated_tokens: int = DEFAULT_STEP_TOKEN_ESTIMATE,
) -> tuple[bool, str]:
    """Whether new work should be refused, and why.

    No meter bound means no ceiling, which is the fail-open case that matters:
    anything that forgot to bind a meter runs unbounded rather than refusing
    every request. See `charge_tokens` for why there is no try/except here.
    """
    meter = _meter.get()
    if meter is None:
        return False, ""
    if meter.steps_exhausted:
        meter.stop("step_limit")
        return True, "step_limit"
    if not meter.can_afford(estimated_tokens):
        meter.stop("token_budget")
        return True, "token_budget"
    return False, ""
