"""Per-node retry and timeout policy for the agent graph.

Why this file exists rather than a `RetryPolicy()` in `graph.py`
--------------------------------------------------------------
Because LangGraph's default `retry_on` does not retry the failures this graph
actually produces. Measured against langgraph 1.2.11, a node raising each of
these with a plain `RetryPolicy(max_attempts=3)`:

    RuntimeError  ("db pool exhausted")   -> 1 attempt  (NOT retried)
    OSError       ("connection reset")     -> 1 attempt  (NOT retried)
    TimeoutError  ("llm provider timed out") -> 1 attempt (NOT retried)
    ConnectionError                         -> 3 attempts
    ValueError / KeyError                   -> 1 attempt  (correct)
    bare Exception                          -> 3 attempts

The first three are the three most likely transient failures in an agent graph
that talks to Postgres and to an LLM router, and none of them is retried. The
reason is `langgraph/_internal/_retry.py::default_retry_on`, which returns False
for `RuntimeError` and `OSError` — and `TimeoutError` subclasses `OSError` in
Python 3.10+, as does `asyncio.TimeoutError` (an alias since 3.11). The default
is defensible for a generic library. Applied blindly here it is close to a
no-op on the failures that matter, which is the worst outcome: it looks
configured and changes nothing.

The default is also wrong in the opposite direction
---------------------------------------------------
`default_retry_on` ends with `return True`, so it is not conservative about the
unknown — it retries anything it does not recognise as permanent. Measured
against this app's real dependencies, that means it *would* repeat:

    litellm.AuthenticationError       (status 401 -- a bad key stays bad)
    litellm.BadRequestError           (400)
    litellm.ContextWindowExceededError (400 -- the prompt will be too long again)
    sqlalchemy.ProgrammingError       (bad SQL)
    sqlalchemy.IntegrityError          (constraint violation; a repeat is a
                                       second write)
    sqlalchemy.DBAPIError              (base of the two above)
    AttributeError
    bare Exception                     (unclassified)

Three attempts against a misconfigured API key is how a deployment mistake turns
into a rate-limit incident, and a retried `IntegrityError` is a duplicate row.
So the predicate below disagrees with upstream in **both** directions, and
`tests/test_node_policies.py` asserts the full disagreement in both directions
rather than only the half that motivates the file.

The other measured trap, which is worse
---------------------------------------
`NodeTimeoutError` **is** passed to `retry_on`, and retrying it *multiplies the
timeout*. A node with `run_timeout=0.05` and `max_attempts=3` took **1.8s** to
fail instead of 0.05s — a 36x blow-up. Scaled to a realistic 120s research
timeout, that is six minutes of a user watching a spinner after the ceiling was
already declared. So the timeout policy and the retry policy have to be written
against each other, and `NodeTimeoutError` is excluded below.

`NodeCancelledError` (a client disconnect) is never offered to `retry_on` at all
— measured: zero calls, one attempt. It is excluded here anyway, because that
being a framework guarantee rather than our code is exactly the kind of thing
that changes between versions, and a retried disconnect is expensive.

The predicate
-------------
Retry only what is plausibly transient AND cheap to repeat:

* transport-level failures — a connection that did not establish, a read that
  timed out. The request almost certainly never reached the far side, so
  repeating it does not double a side effect.
* 429 and 5xx from an HTTP provider, including LiteLLM's wrapper types.

Refuse everything else, explicitly:

* `NodeTimeoutError` — see above.
* `NodeCancelledError` — the user left.
* 4xx other than 429 — a bad request stays bad; retrying it is a way of
  pretending a config error is a blip.
* our own programming-error family (`ValueError`, `TypeError`, `KeyError`,
  `AttributeError`, `IndexError`, `ImportError`, `NameError`, `SyntaxError`,
  `ArithmeticError`, `StopIteration`) — these do not become true on a second
  attempt, and a retry loop around them turns a fast, obvious traceback into a
  slow, confusing one.
* bare `Exception` — the default retries it. An unclassified exception is
  exactly the case where nobody has said it is safe to repeat, so it is not.

That last exclusion is the one that most reduces apparent capability and is the
one worth arguing about: it means a genuinely transient failure that surfaces as
an unclassified `Exception` is not retried. The alternative — retry the unknown —
is how a double-billing bug gets in, and spend is already bounded by
`core/spend.py`, so the ceiling exists elsewhere.

Why not just retry every `RuntimeError`, as "our DB failures are RuntimeErrors"
suggests
---------------------------------------------------------------------
Because in this codebase they are not. Every `RuntimeError` raised under
`app/` is terminal by construction — grep the tree; the list is "all model
groups returned empty output", "provider circuit open", "checkpointer is
closed", "pyotp is not installed", "Supabase upload failed: 404", "embedding
generation returned empty response". Retrying them cannot help, and one of them
is actively harmful: `litellm_client.py:586` raises `RuntimeError` when the
circuit breaker is open, which is the mechanism we built to *stop* hammering a
failing provider. A retry loop around it defeats the breaker on every attempt.

Measuring the dependencies instead of guessing
----------------------------------------------
The first version of this predicate duck-typed on `.status_code` and
`isinstance(exc, (ConnectionError, TimeoutError))`, and got five cases wrong
when measured against what the libraries actually raise:

    litellm.Timeout            carries status_code=408 -> read as a 4xx, refused
    sqlalchemy.OperationalError  no status, not an OSError -> unclassified, refused
    sqlalchemy.InterfaceError    same
    sqlalchemy.InternalError     same
    sqlalchemy.DBAPIError        same

The DB four are the important ones: `OperationalError` is what a dropped or
refused Postgres connection raises, it does **not** subclass `OSError`, and it
carries no status -- so the "just retry connection errors" version silently
refuses every transient database failure in the graph. That is the same shape of
mistake as the LangGraph default, pointed the other way.

`ProgrammingError` and `IntegrityError` are subclasses of `DBAPIError`, so
retrying the base class would retry them too. The list below names the three
retryable subclasses instead of excluding the permanent ones, which keeps the
intent legible and does not depend on ordering.
"""
from __future__ import annotations

from typing import Final

import openai
from langgraph.errors import NodeCancelledError, NodeTimeoutError
from langgraph.types import RetryPolicy, TimeoutPolicy
from litellm.exceptions import (
    InternalServerError,
    RateLimitError,
    ServiceUnavailableError,
)
from sqlalchemy.exc import (
    InterfaceError,
    InternalError,
    OperationalError,
)

# ── Never retried, checked first so nothing below can reach them ────────────
#: A node that blew its ceiling will blow it again; see the module docstring's
#: measurement (0.05s timeout, 1.8s to fail at max_attempts=3).
NEVER_RETRY: Final[tuple[type[BaseException], ...]] = (
    NodeTimeoutError,
    NodeCancelledError,
)

#: Programming errors. True on the first attempt or never; a second attempt
#: only adds latency and hides the traceback.
NON_TRANSIENT: Final[tuple[type[BaseException], ...]] = (
    ValueError,
    TypeError,
    KeyError,
    IndexError,
    AttributeError,
    ImportError,
    NameError,
    SyntaxError,
    ArithmeticError,
    StopIteration,
    StopAsyncIteration,
    ReferenceError,
)

#: Database failures worth repeating.
#:
#: Named subclasses rather than `DBAPIError`, because `ProgrammingError` (bad
#: SQL) and `IntegrityError` (constraint violation, and a repeat risks a
#: duplicate write) are siblings under that base and must not be swept in.
#:
#: * `OperationalError` — connection refused, dropped, or the pool timing out.
#: * `InterfaceError` — the connection died mid-use. The classic case where the
#:   server may or may not have committed, which is why the write-bearing nodes
#:   (`artifact`, `bootstrap`) are NO_RETRY regardless.
#: * `InternalError` — deadlock and serialization failures, which the database
#:   documents as safe to retry.
RETRYABLE_DB: Final[tuple[type[BaseException], ...]] = (
    OperationalError,
    InterfaceError,
    InternalError,
)

#: LLM-router failures worth repeating.
#:
#: This is `openai.APIConnectionError` rather than `litellm.APIConnectionError`,
#: and the difference is not cosmetic. Measured:
#:
#:     litellm.APIConnectionError -> openai.APIConnectionError -> ...
#:     litellm.Timeout            -> openai.APITimeoutError
#:                                -> openai.APIConnectionError -> ...
#:
#: `litellm.Timeout` is **not** a subclass of `litellm.APIConnectionError` — the
#: two sit in parallel under the openai base. So a list built from litellm's own
#: classes misses the single most likely transient failure in this graph, and
#: `litellm.Timeout` was only saved by carrying `status_code=408`. Null that
#: status — which is what happens when a read times out with no HTTP response —
#: and the timeout is refused. That is the exact bug this module exists to fix,
#: reachable again through the hierarchy.
#:
#: One openai base class covers the whole connection-and-timeout family in both
#: hierarchies, including any `openai.*` error litellm re-raises unwrapped.
#: `openai` is a hard dependency of `litellm`, not a new one.
#:
#: The status-carrying classes (`AuthenticationError`, `BadRequestError`,
#: `NotFoundError`, `ContextWindowExceededError`, `PermissionDeniedError`) are
#: verified to sit *outside* `openai.APIConnectionError`, so this does not
#: sweep in a bad credential or a malformed request.
RETRYABLE_LLM: Final[tuple[type[BaseException], ...]] = (
    openai.APIConnectionError,
    RateLimitError,
    ServiceUnavailableError,
    InternalServerError,
)

#: HTTP statuses that mean "later" rather than "never".
#:
#: 429 is the obvious one. **408 is not obvious and is the reason this is a
#: tuple**: `litellm.Timeout` carries `status_code=408`, so a predicate that
#: retries 5xx and 429 and nothing else refuses every LLM timeout — which is the
#: exact bug this module was written to fix, reintroduced one level down.
RETRYABLE_STATUS: Final[frozenset[int]] = frozenset({408, 409, 425, 429})


def _status_of(exc: BaseException) -> int | None:
    """HTTP status carried by an exception, or None.

    Reads `.response.status_code` (httpx / requests) and a bare
    `.status_code` (litellm) rather than hard-coding one library's exception
    hierarchy, because the two disagree and a network failure should not be
    classified differently depending on which client raised it.
    """
    response = getattr(exc, "response", None)
    status = getattr(response, "status_code", None)
    if isinstance(status, int):
        return status
    status = getattr(exc, "status_code", None)
    return status if isinstance(status, int) else None


def retry_on_transient(exc: Exception) -> bool:
    """Whether `exc` is worth repeating. Used as LangGraph's `retry_on`.

    Ordering matters and is asserted by the tests:

    1. Never-retry first. `NodeTimeoutError` is not an `OSError`, but keeping
       these ahead of everything else means a future subclass relationship
       cannot accidentally make one retryable.
    2. Non-transient programming errors next, before any transport check, so a
       `ValueError` raised inside an HTTP client is not mistaken for one.
    3. Retryable database failures.
    4. Retryable LLM-router failures.
    5. An explicit status code, when there is one, decides: 408/409/425/429 and
       5xx retry; every other 4xx does not. This sits above the bare transport
       check so a `ConnectionError` carrying a 404 is not retried.
    6. Then the transport shapes: `ConnectionError` and `TimeoutError` (which is
       `asyncio.TimeoutError`).
    7. Anything unclassified: no.

    Step 6 deliberately does **not** include bare `OSError`. `OSError` is the
    parent of `ConnectionError`, `TimeoutError`, `SSLError` and more, so
    retrying it retries certificate failures and "disk full" too — and those are
    not transient in any sense that repeating the request fixes.
    """
    if isinstance(exc, NEVER_RETRY):
        return False
    if isinstance(exc, NON_TRANSIENT):
        return False
    if isinstance(exc, RETRYABLE_DB):
        return True
    if isinstance(exc, RETRYABLE_LLM):
        return True

    status = _status_of(exc)
    if status is not None:
        return status in RETRYABLE_STATUS or 500 <= status < 600

    return isinstance(exc, (ConnectionError, TimeoutError))


# ── The policies ───────────────────────────────────────────────────────────

#: For nodes whose work is a read or an idempotent call.
#:
#: 0.5s initial, doubling, jittered (LangGraph's own defaults), capped at 3
#: attempts. Worst case adds ~1.5s of sleeping to a failing run, which is
#: invisible next to a single LLM call. `max_attempts` counts TOTAL attempts,
#: not retries -- verified by the tests, because reading it as "3 retries" would
#: quadruple the worst case.
RETRY_TRANSIENT: Final[RetryPolicy] = RetryPolicy(
    max_attempts=3,
    initial_interval=0.5,
    backoff_factor=2.0,
    max_interval=8.0,
    jitter=True,
    retry_on=retry_on_transient,
)

#: For nodes that must not be repeated at all.
#:
#: `max_attempts=1` rather than omitting the policy: `set_node_defaults` applies
#: a default to every node, and a per-node value is the documented way to opt
#: out. `retry_on` is still passed so that the intent is legible at the call
#: site and so a test can assert the node really is excluded rather than
#: relying on the absence of an argument.
NO_RETRY: Final[RetryPolicy] = RetryPolicy(
    max_attempts=1,
    retry_on=retry_on_transient,
)


def _timeout(run_timeout: float, idle_timeout: float | None = None) -> TimeoutPolicy:
    """Build a TimeoutPolicy with the jitter knob off.

    `refresh_on="auto"` is LangGraph's default and the reason `idle_timeout` is
    useful here: the idle clock resets on state writes, on stream output, and on
    any LangChain callback. A researcher that is genuinely working — emitting
    progress, calling tools — therefore does not trip it, while one that is
    wedged on a single silent HTTP read does.
    """
    return TimeoutPolicy(run_timeout=run_timeout, idle_timeout=idle_timeout)


#: Ceilings, in seconds, per node. Chosen from what each node actually does, not
#: spread evenly:
#:
#: * `researcher`-shaped nodes (`tree_of_thoughts`, `orchestrator`,
#:   `subagent_dispatcher`) reason over many LLM calls. Their budgets are
#:   already bounded internally (RESEARCH_MAX_MODEL_CALLS, the critic's
#:   revision loop, core/spend.py), so the ceiling here is a backstop against a
#:   hang, deliberately generous.
#: * `tool_node` fans out to external services; one slow tool should not eat
#:   the whole run.
#: * `synthesizer` runs the bounded critic loop, so it gets the longest ceiling
#:   of the LLM nodes.
#: * `artifact` is short and local — a DB write plus a length check.
#:
#: These interact with NO_RETRY and RETRY_TRANSIENT by construction:
#: `NodeTimeoutError` is excluded from retries, so a breach costs one ceiling,
#: not three.
TIMEOUTS: Final[dict[str, TimeoutPolicy]] = {
    "bootstrap": _timeout(30.0),
    "planner": _timeout(120.0),
    "tree_of_thoughts": _timeout(300.0),
    "orchestrator": _timeout(300.0),
    "subagent_dispatcher": _timeout(300.0),
    "tool_node": _timeout(180.0, idle_timeout=90.0),
    "critic_grader": _timeout(120.0),
    "synthesizer": _timeout(300.0),
    "artifact": _timeout(60.0),
}

#: Nodes that must never be repeated, with the reason each one is different.
#:
#: * `artifact` writes. It looks up `(user_id, conversation_id, title)` and
#:   creates a new version; a retry after a partial failure can leave two
#:   versions of the same document and `artifact_versions` stops being a history.
#: * `synthesizer` owns the bounded critic loop. Retrying the node re-runs the
#:   whole loop, so a transient failure at the end costs a full re-synthesis —
#:   the single most expensive thing in the graph.
#: * `bootstrap` seeds state that every later node reads. Re-running it against
#:   a partially-populated state risks doubling an append.
NO_RETRY_NODES: Final[frozenset[str]] = frozenset(
    {"artifact", "synthesizer", "bootstrap"}
)


def policy_for(node: str) -> RetryPolicy:
    """The retry policy for `node`. Exposed so tests assert the real mapping
    rather than re-deriving it."""
    return NO_RETRY if node in NO_RETRY_NODES else RETRY_TRANSIENT


def timeout_for(node: str) -> TimeoutPolicy:
    """The timeout policy for `node`.

    Raises `KeyError` for an unknown node on purpose. A typo'd node name in
    `TIMEOUTS` would otherwise silently mean "no timeout", which is the exact
    shape of bug this repo has been bitten by twice (§2 checkpointer, §9.14
    mock-hides-logic). `add_node` on an unregistered name raises anyway, so this
    turns a silent gap into a loud one at the point of use.
    """
    return TIMEOUTS[node]


__all__ = [
    "NEVER_RETRY",
    "NON_TRANSIENT",
    "NO_RETRY",
    "NO_RETRY_NODES",
    "RETRYABLE_DB",
    "RETRYABLE_LLM",
    "RETRYABLE_STATUS",
    "RETRY_TRANSIENT",
    "TIMEOUTS",
    "policy_for",
    "retry_on_transient",
    "timeout_for",
]
