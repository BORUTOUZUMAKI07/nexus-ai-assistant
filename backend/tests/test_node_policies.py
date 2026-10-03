"""Per-node retry and timeout policy: what is attached, and what it does.

Two halves, and the second is the one that matters.

The first half asserts the wiring — that `_build_workflow()` really hands
LangGraph a retry policy and a timeout for every node, with the intended
per-node overrides. This is the `isinstance` lesson from §2: a policy module
full of correct values that nothing passes to `compile()` looks identical from
the outside to one that is wired.

The second half runs the failures. A predicate that reads correctly and returns
the wrong answer for `TimeoutError` is the whole reason this file exists, and
only executing it distinguishes the two. Every count below is measured, not
asserted from reading the source:

    node raising RuntimeError / OSError / TimeoutError, max_attempts=3
      LangGraph default retry_on -> 1 attempt each  (never retried)
      retry_on_transient        -> 3 attempts each

    NodeTimeoutError with run_timeout=0.05, max_attempts=3
      default retry_on          -> 3 attempts, 1.8s to fail (36x the ceiling)
      retry_on_transient        -> 1 attempt,  0.05s to fail

Run: `cd backend && uv run pytest tests/test_node_policies.py`
"""
from __future__ import annotations

import asyncio
import time

import pytest
from backend.app.agents.orchestrator.graph import _build_workflow
from backend.app.agents.orchestrator.retry_policy import (
    NO_RETRY,
    NO_RETRY_NODES,
    NON_TRANSIENT,
    RETRY_TRANSIENT,
    TIMEOUTS,
    _status_of,
    policy_for,
    retry_on_transient,
    timeout_for,
)
from langgraph.errors import NodeCancelledError, NodeTimeoutError
from langgraph.graph import END, START, StateGraph
from langgraph.types import RetryPolicy
from typing_extensions import TypedDict

#: RETRY_TRANSIENT with the backoff compressed, so two retries cost ~30ms
#: instead of ~1.5s. Same `retry_on` object and same `max_attempts`, so the
#: attempt counts asserted below are the counts production would produce;
#: `test_the_compression_in_FAST_does_not_change_the_verdict` pins that.
FAST = RetryPolicy(
    max_attempts=3,
    initial_interval=0.01,
    max_interval=0.02,
    jitter=False,
    retry_on=retry_on_transient,
)


class _State(TypedDict, total=False):
    marker: str


async def _run(node_body, policy: RetryPolicy | None, timeout=None) -> tuple[int, str]:
    """Run a one-node graph. Returns (attempts, terminal exception name)."""
    attempts = {"n": 0}

    async def _n(state):
        attempts["n"] += 1
        return await node_body(state)

    kwargs = {}
    if policy is not None:
        kwargs["retry_policy"] = policy
    if timeout is not None:
        kwargs["timeout"] = timeout

    workflow = StateGraph(_State)
    workflow.add_node("n", _n, **kwargs)
    workflow.add_edge(START, "n")
    workflow.add_edge("n", END)

    try:
        await workflow.compile().ainvoke({"marker": "x"})
        return attempts["n"], "no-raise"
    except BaseException as exc:  # noqa: BLE001 -- the terminal type is the datum
        return attempts["n"], type(exc).__name__


def _raiser(exc: Exception):
    async def _body(state):
        raise exc

    return _body


# ── The predicate ──────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "exc",
    [
        ConnectionError("refused"),
        TimeoutError("llm provider timed out"),
        asyncio.TimeoutError("same class since 3.11"),
    ],
    ids=["connection", "timeout", "asyncio-timeout"],
)
def test_transport_failures_are_retried(exc: Exception):
    """These are the three the LangGraph default misses.

    `TimeoutError` subclasses `OSError`, and `default_retry_on` returns False for
    `OSError` -- so an LLM provider timeout expressed as a builtin
    `TimeoutError` is never retried by a bare `RetryPolicy()`. That is the
    specific gap this predicate closes.
    """
    assert retry_on_transient(exc) is True


# ── What the real dependencies raise ────────────────────────────────────────
#
# These four blocks exist because the first version of the predicate duck-typed
# on `.status_code` plus `isinstance(exc, (ConnectionError, TimeoutError))` and
# got every one of these wrong when measured. Each case below was observed
# failing, not predicted.


def test_a_transient_database_failure_is_retried():
    """`OperationalError` is what a refused or dropped Postgres connection
    raises. It is NOT an `OSError` and carries no status code, so the naive
    predicate classified every transient database failure as unclassified and
    refused it -- the same mistake as the LangGraph default, pointed the other
    way."""
    from sqlalchemy.exc import InterfaceError, InternalError, OperationalError

    assert issubclass(OperationalError, Exception)
    assert not issubclass(OperationalError, OSError), (
        "SQLAlchemy changed its hierarchy; re-measure OperationalError."
    )
    for cls in (OperationalError, InterfaceError, InternalError):
        assert retry_on_transient(cls("SELECT 1", {}, Exception("boom"))) is True


def test_a_permanent_database_failure_is_not_retried():
    """The siblings that must stay out.

    `ProgrammingError` and `IntegrityError` are both subclasses of `DBAPIError`,
    so retrying the base class would sweep them in. `IntegrityError` matters
    most: a constraint violation retried is a duplicate write.
    """
    from sqlalchemy.exc import DBAPIError, IntegrityError, ProgrammingError

    assert issubclass(ProgrammingError, DBAPIError)
    assert issubclass(IntegrityError, DBAPIError)

    stmt = object()
    for cls in (ProgrammingError, IntegrityError):
        exc = cls("stmt", {}, Exception("boom"), stmt)
        assert retry_on_transient(exc) is False, f"{cls.__name__} must not retry"


def test_an_llm_timeout_is_retried_despite_carrying_a_4xx():
    """The subtle one, and the reason `RETRYABLE_STATUS` is a set.

    `litellm.Timeout` carries `status_code=408`. A predicate that retries 5xx
    and 429 refuses it -- reintroducing the exact bug this module exists to fix,
    one level down. Measured, not assumed.
    """
    import litellm

    exc = litellm.Timeout("slow", model="m", llm_provider="openai")
    assert getattr(exc, "status_code", None) == 408, (
        "litellm changed Timeout's status; re-measure RETRYABLE_STATUS."
    )
    assert retry_on_transient(exc) is True


@pytest.mark.parametrize(
    ("name", "retryable"),
    [
        ("APIConnectionError", True),
        ("RateLimitError", True),
        ("ServiceUnavailableError", True),
        ("InternalServerError", True),
        ("Timeout", True),
        ("AuthenticationError", False),
        ("BadRequestError", False),
        ("NotFoundError", False),
        ("ContextWindowExceededError", False),
    ],
)
def test_llm_router_errors_are_classified_by_whether_a_repeat_can_help(name, retryable):
    """The LLM router's own taxonomy.

    `AuthenticationError` in particular must not retry: the bad credential is
    still bad, and three attempts against it is how a misconfigured key becomes
    a rate-limit incident. `ContextWindowExceededError` must not retry either --
    the prompt is too long and will be too long next time.
    """
    import litellm

    cls = getattr(litellm, name)
    exc = cls("boom", model="m", llm_provider="openai")
    assert retry_on_transient(exc) is retryable


@pytest.mark.parametrize(
    "exc",
    [
        RuntimeError("all model groups returned empty output"),
        ValueError("bad input"),
        TypeError(None),
        KeyError("missing"),
        IndexError("out of range"),
        AttributeError("no such attr"),
        ImportError("no module"),
        NameError("undefined"),
        SyntaxError("bad source"),
        ArithmeticError("divide"),
        StopIteration("generator"),
        StopAsyncIteration("agen"),
        ReferenceError("weakref"),
        Exception("unclassified"),
        OSError("disk full"),
    ],
    ids=[
        "runtime",
        "value",
        "type",
        "key",
        "index",
        "attribute",
        "import",
        "name",
        "syntax",
        "arithmetic",
        "stopiter",
        "stopaiter",
        "reference",
        "unclassified",
        "bare-oserror",
    ],
)
def test_non_transient_failures_are_refused(exc: Exception):
    """Programming errors are true on the first attempt or never.

    `RuntimeError` is here, and the reason is specific rather than general: every
    `RuntimeError` raised under `app/` is terminal by construction. Two examples
    that decide it:

    * `litellm_client.py:522` — "All model groups returned empty output". A second
      attempt gets the same empty output.
    * `litellm_client.py:586` — "Provider circuit open". This is the breaker we
      built to *stop* hammering a failing provider, so retrying it defeats the
      mechanism on every attempt.

    Treating `RuntimeError` as "a database failure" -- which is what the LangGraph
    default's exclusion list implies -- would retry both.
    """
    assert retry_on_transient(exc) is False


def test_every_runtimeerror_raised_in_app_is_terminal():
    r"""Guards the claim above against the codebase changing under it.

    Walks `app/` with `ast` and collects the *messages* of every `raise
    RuntimeError(...)`. If someone starts raising it for a transient condition,
    this test fails and the reasoning above has to be redone. `ast` rather than
    a regex because this repo has CRLF checkouts, where `\s*\n\s*` matches
    nothing and a grep-based check silently reports success (AGENTS.md §2).
    """
    import ast
    from pathlib import Path

    app = Path(__file__).resolve().parents[1] / "app"
    messages: list[str] = []
    for path in app.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Raise) or node.exc is None:
                continue
            call = node.exc.func if isinstance(node.exc, ast.Call) else node.exc
            name = getattr(call, "id", None) or getattr(call, "attr", None)
            if name != "RuntimeError":
                continue
            first = node.exc.args[0] if isinstance(node.exc, ast.Call) and node.exc.args else None
            messages.append(
                f"{path.relative_to(app)}:{node.lineno}: "
                f"{ast.unparse(first)[:70] if first is not None else '<no message>'}"
            )

    assert messages, "no RuntimeError raises found -- the scan is broken, not the claim"
    # Spot-check the two that decide the policy. If either is reworded into a
    # genuinely transient condition, this is the line that should stop someone.
    joined = "\n".join(messages)
    assert "circuit open" in joined, (
        "the circuit-breaker RuntimeError moved or was reworded; re-check "
        "whether RuntimeError is still safe to refuse"
    )
    assert "empty output" in joined


def test_a_bare_oserror_is_refused_while_connection_error_is_not():
    """The distinction that makes the `OSError` refusal defensible.

    `ConnectionError` and `TimeoutError` are both `OSError` subclasses. Retrying
    `OSError` wholesale would also retry `ssl.SSLError` (a certificate problem,
    never transient) and `OSError("no space left on device")`. Splitting them is
    the whole point -- so this asserts the split survives a well-meaning future
    edit that widens the tuple.
    """
    assert issubclass(ConnectionError, OSError)
    assert issubclass(TimeoutError, OSError)
    assert retry_on_transient(ConnectionError("x")) is True
    assert retry_on_transient(TimeoutError("x")) is True
    assert retry_on_transient(OSError("x")) is False


def test_a_status_code_decides_before_the_transport_check():
    """HTTP-shaped failures carry their own answer, and it wins.

    Ordered before the transport check so a `ConnectionError` subclass carrying
    a 404 does not get retried on the strength of being a connection error.

    The `408` / `409` / `425` cases are the reason `RETRYABLE_STATUS` is a set
    rather than `status == 429 or 500 <= status < 600`. They are not covered by
    the litellm tuple either: this exception is a plain HTTP-shaped error, the
    kind an httpx/requests client raises for the Firecrawl and storage calls,
    and it is not an `openai.APIConnectionError`. A revert harness removed 408
    and the suite stayed green until these three lines existed, because
    `litellm.Timeout` was reaching the retry through the LLM tuple instead.
    """

    class _Resp:
        def __init__(self, code: int) -> None:
            self.status_code = code

    class _HTTPError(Exception):
        def __init__(self, code: int) -> None:
            self.response = _Resp(code)

    assert retry_on_transient(_HTTPError(429)) is True, "429 means 'later', not 'never'"
    assert retry_on_transient(_HTTPError(408)) is True, "request timed out, resend"
    assert retry_on_transient(_HTTPError(409)) is True, "conflict, re-read and retry"
    assert retry_on_transient(_HTTPError(425)) is True, "too early, try again"
    assert retry_on_transient(_HTTPError(500)) is True
    assert retry_on_transient(_HTTPError(502)) is True
    assert retry_on_transient(_HTTPError(599)) is True
    assert retry_on_transient(_HTTPError(400)) is False
    assert retry_on_transient(_HTTPError(401)) is False, "a bad token stays bad"
    assert retry_on_transient(_HTTPError(403)) is False
    assert retry_on_transient(_HTTPError(404)) is False
    assert retry_on_transient(_HTTPError(600)) is False, "not a real status"


def test_a_bare_status_code_attribute_also_counts():
    """LiteLLM raises with `.status_code` directly, no `.response` wrapper."""

    class _LLMError(Exception):
        status_code = 503

    assert retry_on_transient(_LLMError()) is True


@pytest.mark.parametrize(
    "make_exc",
    [
        lambda: __import__("openai").APIConnectionError(request=None),
        lambda: __import__("openai").APITimeoutError(request=None),
        lambda: __import__("litellm").APIConnectionError(
            "x", model="m", llm_provider="p"
        ),
        lambda: __import__("litellm").Timeout("x", model="m", llm_provider="p"),
    ],
    ids=[
        "openai-connection",
        "openai-timeout",
        "litellm-connection",
        "litellm-timeout",
    ],
)
def test_an_llm_connection_failure_with_no_status_is_still_retried(make_exc):
    r"""The case `RETRYABLE_LLM` exists for, and the reason it lists an openai class.

    A revert harness deleted `RETRYABLE_LLM` outright and the suite stayed green,
    because every test above used an exception *constructed* with a status code:
    `litellm.APIConnectionError()` carries 500, `RateLimitError` 429, and so on,
    so the status branch answered for them. That made the whole tuple look
    redundant.

    It is not. A real connection failure has no HTTP response, so there is no
    status to read, and `openai.APIConnectionError` is not an `OSError` -- so
    without this tuple every one of these falls through to `False`. Setting
    `status_code = None` reproduces the no-response case without a network.

    `litellm.Timeout` is the one that forced an openai base class: measured,
    `litellm.Timeout -> openai.APITimeoutError -> openai.APIConnectionError`,
    which is a *parallel* branch to `litellm.APIConnectionError`. A tuple of
    litellm's own classes misses it entirely.
    """
    exc = make_exc()
    assert _status_of(exc) == 408 or _status_of(exc) in (None, 500), "unexpected default"

    # Null the status: this is what a timeout or a refused TCP connection
    # actually looks like, and it removes the status branch from the answer.
    try:
        exc.status_code = None
    except AttributeError:
        pass
    if getattr(exc, "response", None) is not None:
        try:
            exc.response = None
        except AttributeError:
            pass

    assert _status_of(exc) is None, "the status branch is still reachable; test is not testing what it claims"
    assert not isinstance(exc, (ConnectionError, TimeoutError)), (
        "these now look like builtin transport errors, so the tuple is no "
        "longer the only thing retrying them"
    )
    assert retry_on_transient(exc) is True, (
        "an LLM connection failure with no status must be retried -- this is "
        "the transient case the module exists for"
    )


def test_the_retryable_llm_tuple_covers_both_openai_hierarchies():
    """Pins the hierarchy fact that the fix depends on.

    `litellm.APIConnectionError` is a subclass of `openai.APIConnectionError`;
    `litellm.Timeout` is not a subclass of either litellm connection class but
    *is* an `openai.APITimeoutError`, hence an `openai.APIConnectionError`. If a
    future litellm reorganises these, the tuple stops covering the family and
    this fails rather than silently refusing timeouts again.
    """
    import litellm
    import openai
    from backend.app.agents.orchestrator.retry_policy import RETRYABLE_LLM

    assert issubclass(litellm.APIConnectionError, openai.APIConnectionError)
    assert not issubclass(litellm.Timeout, litellm.APIConnectionError), (
        "litellm.Timeout is now under litellm.APIConnectionError; the openai "
        "base class may no longer be necessary -- re-measure"
    )
    assert issubclass(litellm.Timeout, openai.APIConnectionError)
    assert openai.APIConnectionError in RETRYABLE_LLM

    # And the status-carrying classes stay outside it, so a bad key or a
    # malformed request is not swept in by the broadened base.
    for name in (
        "AuthenticationError",
        "BadRequestError",
        "NotFoundError",
        "ContextWindowExceededError",
    ):
        cls = getattr(litellm, name)
        assert not issubclass(cls, openai.APIConnectionError), (
            f"{name} is now inside openai.APIConnectionError; RETRYABLE_LLM "
            f"would retry it"
        )



def test_a_non_integer_status_is_ignored_not_crashed_on():
    """`response` present but carrying something unexpected must not raise out
    of a retry predicate -- an exception here would replace the real failure
    with a confusing one."""

    class _UnparseableStatusError(Exception):
        response = object()

    assert retry_on_transient(_UnparseableStatusError()) is False


def test_never_retry_is_checked_before_the_transport_shapes():
    """Ordering, asserted rather than assumed.

    `NodeTimeoutError` is not currently an `OSError`, so a naive test would pass
    for the wrong reason: the predicate's final fallthrough also returns False.
    Reverting `NEVER_RETRY` to `(NodeCancelledError,)` left the suite green --
    the harness caught that, and this test is the fix.

    So the objects here are **deliberately given the shape that would otherwise
    be retried**: each is mixed with `TimeoutError`, making it an `OSError`
    subclass. Without the `NEVER_RETRY` check these return True and a timeout
    gets retried `max_attempts` times, multiplying the ceiling. With it, they
    are refused by the first branch -- which is the only branch that can refuse
    them.
    """
    assert issubclass(NodeTimeoutError, Exception)
    assert issubclass(NodeCancelledError, Exception)

    for base in (NodeTimeoutError, NodeCancelledError):
        # A legal MRO: several langgraph errors derive from `Exception` and
        # `TimeoutError` derives from `OSError`, so both can be combined.
        transient_shaped = type(f"_Transient{base.__name__}", (base, TimeoutError), {})
        assert issubclass(transient_shaped, OSError), (
            "the mix did not produce an OSError; the test is not exercising "
            "the branch it claims to"
        )
        exc = transient_shaped.__new__(transient_shaped)
        Exception.__init__(exc, "node-name")
        assert retry_on_transient(exc) is False, (
            f"{base.__name__} with a transient shape must still be refused"
        )
        # And prove the shape alone would have been retried, so the assertion
        # above is not vacuous.
        assert retry_on_transient(TimeoutError("plain")) is True


def test_non_transient_is_checked_before_the_status_code():
    """A `ValueError` raised *inside* an HTTP client carries a 503. It is still
    a bug, not a blip, and the programming-error family must win."""

    class _Resp:
        status_code = 503

    class _BugCarryingValueError(ValueError):
        response = _Resp()

    assert issubclass(_BugCarryingValueError, ValueError)
    assert retry_on_transient(_BugCarryingValueError()) is False


# ── The behaviour ──────────────────────────────────────────────────────────


def _operational_error():
    from sqlalchemy.exc import OperationalError

    return OperationalError("SELECT 1", {}, Exception("connection refused"))


def _litellm_timeout():
    import litellm

    return litellm.Timeout("slow", model="m", llm_provider="p")


def _litellm_connection():
    import litellm

    return litellm.APIConnectionError("refused", model="m", llm_provider="p")


@pytest.mark.parametrize(
    "make_exc",
    [
        lambda: ConnectionError("refused"),
        lambda: TimeoutError("llm timed out"),
        lambda: asyncio.TimeoutError("asyncio timed out"),
        _operational_error,
        _litellm_timeout,
        _litellm_connection,
    ],
    ids=[
        "connection",
        "builtin-timeout",
        "asyncio-timeout",
        "operationalerror",
        "litellm-timeout",
        "litellm-connection",
    ],
)
async def test_our_policy_retries_transient_failures(make_exc):
    """The positive half, executed rather than read.

    Every case is one this graph can genuinely produce: a refused connection, an
    LLM call that timed out, a Postgres connection that dropped.
    """
    attempts, terminal = await _run(_raiser(make_exc()), FAST)
    assert attempts == 3, f"expected 3 attempts, got {attempts}"
    assert terminal == type(make_exc()).__name__


async def test_the_langgraph_default_never_retries_a_timeout():
    """The specific gap, stated as the absence of a capability.

    `TimeoutError` subclasses `OSError`, `default_retry_on` returns False for
    `OSError`, so *no* amount of `max_attempts` makes the upstream default retry
    a timeout. The builtin and the asyncio alias are the same class since 3.11,
    so this is one fact rather than two.
    """
    from langgraph._internal._retry import default_retry_on

    assert issubclass(asyncio.TimeoutError, TimeoutError), "3.11+ alias changed"
    assert issubclass(TimeoutError, OSError)

    for exc in (TimeoutError("x"), asyncio.TimeoutError("x")):
        assert default_retry_on(exc) is False
        assert retry_on_transient(exc) is True

    # And end to end through the real machinery, not just the predicate.
    attempts, _ = await _run(
        _raiser(TimeoutError("x")),
        RetryPolicy(max_attempts=3, initial_interval=0.01, jitter=False),
    )
    assert attempts == 1, (
        "upstream now retries timeouts; a bare RetryPolicy() would suffice and "
        "this whole predicate should be reconsidered"
    )


async def test_a_non_transient_failure_is_attempted_exactly_once():
    attempts, terminal = await _run(_raiser(ValueError("bad")), FAST)
    assert attempts == 1
    assert terminal == "ValueError"


async def test_no_retry_policy_gives_one_attempt_even_for_a_transient_error():
    """How `synthesizer` / `artifact` / `bootstrap` are excluded.

    They must not be retried even for a failure that WOULD qualify -- that is
    the whole content of NO_RETRY_NODES.
    """
    attempts, _ = await _run(_raiser(ConnectionError("refused")), NO_RETRY)
    assert attempts == 1
    attempts, _ = await _run(_raiser(TimeoutError("t")), NO_RETRY)
    assert attempts == 1


async def test_a_node_timeout_is_not_retried_and_does_not_multiply_the_ceiling():
    """The worst measured interaction: 0.05s ceiling, 1.8s to fail.

    Retrying `NodeTimeoutError` means the user waits `max_attempts` times the
    ceiling they were promised. Scaled to a 120s research timeout that is six
    minutes of spinner. This is the assertion that keeps `NEVER_RETRY` honest.
    """
    from langgraph.types import TimeoutPolicy

    async def _slow(state):
        await asyncio.sleep(1.0)
        return state

    started = time.monotonic()
    attempts, terminal = await _run(
        _slow, FAST, timeout=TimeoutPolicy(run_timeout=0.05, idle_timeout=None)
    )
    elapsed = time.monotonic() - started

    assert terminal == "NodeTimeoutError"
    assert attempts == 1, f"a timeout was retried {attempts} times"
    # Generous upper bound: the point is "about one ceiling", not "0.05s exactly"
    # on a loaded CI box. A retried version took ~1.8s.
    assert elapsed < 0.6, f"timeout took {elapsed:.2f}s -- it is being multiplied"


async def test_a_client_disconnect_is_never_retried():
    attempts, terminal = await _run(
        _raiser(asyncio.CancelledError()), FAST
    )
    assert attempts == 1
    assert terminal == "NodeCancelledError"

    # And the predicate would refuse it even if LangGraph started offering it:
    # today it is never offered at all, which is a framework guarantee rather
    # than our code, and therefore not one to rely on alone.
    assert retry_on_transient(NodeCancelledError("n")) is False


async def test_max_attempts_counts_total_attempts_not_retries():
    """`max_attempts=3` means three tries, not one try plus three retries.

    Reading it the other way makes the worst case four times larger than
    intended, which is the kind of misreading that only shows up in an incident.
    """
    attempts, _ = await _run(_raiser(ConnectionError("x")), FAST)
    assert attempts == FAST.max_attempts


async def test_backoff_actually_sleeps_between_attempts():
    r"""The gaps grow, and the total is close to the sum of the nominal gaps.

    Uses its own policy with intervals large enough that scheduler noise is
    negligible. The first version reused `FAST` (gaps 0.01s and 0.02s) and
    asserted `gap2 >= gap1 * 0.9`; it passed in isolation and failed in the full
    suite, because a 10ms sleep under load is mostly the timer granularity. That
    is the flakiness the docstring warned about and then committed anyway: the
    assertion has to be loose *relative to the noise floor*, and 10ms is at the
    noise floor.

    100ms/200ms is ~20x the noise, so `gap2 > gap1 * 1.5` is not a timing
    assertion in any meaningful sense -- a non-backing-off implementation would
    produce equal gaps and fail it.
    """
    policy = RetryPolicy(
        max_attempts=3,
        initial_interval=0.1,
        backoff_factor=2.0,
        max_interval=0.4,
        jitter=False,
        retry_on=retry_on_transient,
    )
    stamps: list[float] = []

    async def _body(state):
        stamps.append(time.monotonic())
        raise ConnectionError("x")

    await _run(_body, policy)
    assert len(stamps) == 3
    gap1 = stamps[1] - stamps[0]
    gap2 = stamps[2] - stamps[1]
    assert gap1 > 0.0
    assert gap2 > gap1 * 1.5, f"no backoff: gap1={gap1:.3f}s gap2={gap2:.3f}s"
    # And the total is at least the sum of the nominal gaps (0.1 + 0.2 = 0.3),
    # which a fixed 0.1s interval (total 0.2) would fail.
    assert (stamps[2] - stamps[0]) > 0.25


# ── The wiring ─────────────────────────────────────────────────────────────


def test_every_node_has_a_policy_entry():
    """TIMEOUTS and the graph must agree, in both directions.

    Forward: a node with no ceiling ships unbounded.
    Reverse: an entry for a node that no longer exists is dead config that looks
    live -- the same failure as the three deleted YAML files in backend/config/.
    """
    workflow = _build_workflow()
    registered = set(workflow.nodes)
    assert set(TIMEOUTS) == registered


def test_the_compiled_graph_really_carries_the_policies():
    """The §2 lesson: correct values that nothing passes to `compile()`.

    `add_node` stores the policy on the builder node; this reads it back off the
    compiled graph's own node specs. If someone drops the kwargs in `_add`, this
    fails -- a grep for `retry_policy=` in graph.py would not.
    """
    compiled = _build_workflow().compile()
    specs = compiled.nodes

    for name in sorted(TIMEOUTS):
        spec = specs[name]
        assert spec.timeout is not None, f"{name} compiled with no timeout"
        assert spec.retry_policy is not None, f"{name} compiled with no retry policy"

        expected_policy = NO_RETRY if name in NO_RETRY_NODES else RETRY_TRANSIENT
        actual_max = _max_attempts(spec.retry_policy)
        assert actual_max == expected_policy.max_attempts, (
            f"{name}: compiled max_attempts={actual_max}, "
            f"policy_for() says {expected_policy.max_attempts}"
        )


def _max_attempts(policy) -> int:
    """Max attempts from a policy that may be a single policy or a sequence.

    LangGraph matches a LIST of policies against an exception in order
    (verified in pregel/_retry.py:805), so `add_node` legitimately accepts both
    shapes. Reading `.max_attempts` off a list would silently pass the wrong
    thing, which is why this exists.
    """
    if isinstance(policy, (list, tuple)):
        assert policy, "an empty retry_policy list disables retrying entirely"
        return max(getattr(p, "max_attempts", 1) for p in policy)
    return getattr(policy, "max_attempts", 1)


def test_the_write_nodes_are_the_ones_excluded_from_retry():
    """Pins the *reason*, not just the membership.

    A future edit that removes `artifact` from NO_RETRY_NODES would still pass a
    membership test against the new set. Asserting the specific nodes that must
    never be repeated is what makes the exclusion deliberate.
    """
    assert NO_RETRY_NODES == {"artifact", "synthesizer", "bootstrap"}
    assert policy_for("artifact") is NO_RETRY
    assert policy_for("synthesizer") is NO_RETRY
    assert policy_for("bootstrap") is NO_RETRY
    # And the rest do retry.
    for name in ("planner", "orchestrator", "subagent_dispatcher", "tool_node"):
        assert policy_for(name) is RETRY_TRANSIENT


def test_a_node_with_no_timeout_entry_raises_instead_of_defaulting():
    """`timeout_for` must not have a `.get(name, default)` fallback.

    A silent "no ceiling" default is precisely the bug this helper exists to
    prevent, so the loud failure is the feature.
    """
    with pytest.raises(KeyError):
        timeout_for("no_such_node")


def test_the_default_policy_is_ours_not_langgraphs():
    """`set_node_defaults` must receive RETRY_TRANSIENT.

    Asserted on the object identity of the predicate, because that is the part
    that differs from the default -- `max_attempts` is 3 either way, so a test
    on the number alone would pass with LangGraph's policy in place.

    The rest is the complete disagreement with upstream, in both directions.
    `default_retry_on` ends with `return True`, so it is not conservative about
    the unknown -- it retries anything it does not explicitly recognise as
    permanent. Measured, both columns:

        default True,  ours False:  Exception, AttributeError,
                                      AuthenticationError, BadRequestError,
                                      ContextWindowExceededError,
                                      ProgrammingError, IntegrityError, DBAPIError
        default False, ours True:   TimeoutError (and asyncio.TimeoutError)

    Eleven of NON_TRANSIENT's twelve are refused by both; only
    `AttributeError` is a disagreement, and it is on the list below rather than
    in a summary, because upstream's exclusion list is narrower than ours and
    asserting a count over it would go stale on the next upstream edit.
    """
    import litellm
    from langgraph._internal._retry import default_retry_on
    from sqlalchemy.exc import DBAPIError, IntegrityError, ProgrammingError

    assert RETRY_TRANSIENT.retry_on is retry_on_transient
    assert RETRY_TRANSIENT.retry_on is not default_retry_on

    # Upstream will not retry a timeout; we will.
    assert default_retry_on(TimeoutError("x")) is False
    assert retry_on_transient(TimeoutError("x")) is True

    # Upstream retries each of these; we do not.
    db = ("SELECT 1", {}, Exception("boom"), object())
    too_risky = [
        Exception("unclassified"),
        litellm.AuthenticationError("bad key", model="m", llm_provider="p"),
        litellm.BadRequestError("malformed", model="m", llm_provider="p"),
        litellm.ContextWindowExceededError("too long", model="m", llm_provider="p"),
        ProgrammingError(*db),
        IntegrityError(*db),
        DBAPIError(*db),
    ]
    for exc in too_risky:
        assert default_retry_on(exc) is True, (
            f"upstream stopped retrying {type(exc).__name__}; re-measure -- one "
            f"fewer reason to have a custom predicate"
        )
        assert retry_on_transient(exc) is False

    # Cases both agree on, so the predicate is not simply inverted.
    for exc in (
        ConnectionError("x"),
        _operational_error(),
        _litellm_timeout(),
        RuntimeError("x"),
        ValueError("x"),
        OSError("x"),
    ):
        assert default_retry_on(exc) == retry_on_transient(exc), (
            f"{type(exc).__name__} disagreement changed"
        )

    # `AttributeError` is the one member of the programming-error family that
    # upstream retries. Measured across all twelve, so a future upstream change
    # to the rest shows up here as a failure rather than as silence.
    upstream_retries = {cls.__name__ for cls in NON_TRANSIENT if default_retry_on(cls())}
    assert upstream_retries == {"AttributeError"}, (
        f"upstream's programming-error exclusions changed: now {upstream_retries}. "
        f"Re-read default_retry_on and update this test deliberately."
    )
    for cls in NON_TRANSIENT:
        assert retry_on_transient(cls()) is False


def test_the_compression_in_the_test_policy_does_not_change_the_verdict():
    """`FAST` differs from `RETRY_TRANSIENT` only in sleep durations.

    If someone edited FAST's `retry_on`, every count-based test above would
    start measuring a different predicate than production uses. This asserts the
    two agree on every case the other tests rely on.
    """
    assert FAST.retry_on is RETRY_TRANSIENT.retry_on
    assert FAST.max_attempts == RETRY_TRANSIENT.max_attempts
