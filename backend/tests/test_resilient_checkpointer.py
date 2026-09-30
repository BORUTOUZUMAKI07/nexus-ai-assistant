"""
Regression tests for the reconnecting LangGraph checkpointer.

Why these exist
───────────────
A live stream died with::

    psycopg.OperationalError: consuming input failed:
    server closed the connection unexpectedly

raised from ``AsyncPostgresSaver.aget_tuple`` on the *first* read of the graph,
28ms into the request — before any agent work ran. The cause was structural,
not a bad query: ``AsyncPostgresSaver`` is bound to a single psycopg
``AsyncConnection`` with no pool API, so ``lifespan_graph`` holds one socket
for the whole process. Pointed at Supabase's transaction-mode pooler
(port 6543), that socket was reaped by the server while idle, and the next
chat message inherited a dead transport.

The fix is two independent halves, and each is tested here separately because
either one alone leaves a failure mode:

  * ``test_dsn_*`` (in tests for graph._resolve_checkpoint_dsn) — the port
    upgrade, so the pooler is not given the chance in the first place.
  * ``test_reconnect_*`` — the runtime replacement, for when it is reaped
    anyway (pooler timer, network blip, server restart).

The reconnect tests use a fake connection, a fake saver and an injected
connector rather than a real database: the behaviour under test is "does it
notice a dead socket and build a new one", and a live Postgres would not let a
test kill a connection deterministically. ``test_forwards_every_method_*``
covers the seam the fakes cannot — that the names we forward still exist on
the real ``AsyncPostgresSaver``.

These are ``async def`` tests rather than sync tests calling an ``_await``
helper (the other style used in this suite) because the reconnect path takes
an ``asyncio.Lock``, and a lock binds to the loop it is first awaited on.
Reusing one loop across tests makes the reconnection tests fail for reasons
unrelated to what they check. ``asyncio_mode = "auto"``, so no marker needed.
"""
from __future__ import annotations

import asyncio
import inspect

import pytest
from backend.app.agents.orchestrator.resilient_checkpointer import (
    FORWARDED_METHODS,
    ResilientPostgresSaver,
    connection_is_dead,
)
from psycopg import OperationalError


class FakeConn:
    """Stand-in for psycopg's AsyncConnection, exposing only what we read."""

    def __init__(self, label: str = "conn") -> None:
        self.label = label
        self.closed = False
        self.broken = False
        self.close_calls = 0

    async def close(self) -> None:
        self.close_calls += 1
        self.closed = True


class FakeSaver:
    """
    Stand-in for AsyncPostgresSaver. Records which conn it was built with.

    `kill_conn_on_raise` models the one behaviour the wrapper's correctness
    depends on: when psycopg's transport dies mid-query, the connection object
    reports itself `broken` as part of raising. Without that, every
    reconnect test would be asserting behaviour the code deliberately does not
    have -- the wrapper requires the connection to *confirm* it is dead before
    it treats an OperationalError as a dead transport, because psycopg uses that
    same exception class for ordinary server-side faults.

    A ValueError (a bad query) never kills the socket, so that path leaves
    `kill_conn_on_raise` off.
    """

    instances: list["FakeSaver"] = []

    def __init__(self, conn) -> None:
        self.conn = conn
        self.calls: list[str] = []
        # Set to an exception to make the next call raise it.
        self.raise_next: BaseException | None = None
        self.kill_conn_on_raise = False
        FakeSaver.instances.append(self)

    def _maybe_raise(self):
        if self.raise_next is None:
            return
        err, self.raise_next = self.raise_next, None
        if self.kill_conn_on_raise:
            self.conn.broken = True
        raise err

    async def aget_tuple(self, config):
        self.calls.append("aget_tuple")
        self._maybe_raise()
        return {"thread_id": config["configurable"]["thread_id"]}

    async def aput(self, config, checkpoint, metadata, new_versions):
        self.calls.append("aput")
        self._maybe_raise()
        return None


@pytest.fixture(autouse=True)
def _reset_fake_savers():
    FakeSaver.instances = []
    yield
    FakeSaver.instances = []


def _build(conn: FakeConn, dsn: str = "postgresql://u:p@h:5432/db"):
    """
    Build a wrapper whose reconnects hand back FakeConns, plus the list of
    connections it opened.

    `connector` is the injection point the wrapper exposes for exactly this:
    the behaviour under test is "notice a dead socket, build a new one", which
    needs no database and must not touch the network.
    """
    opened: list[FakeConn] = []

    async def connector(*_args, **_kwargs):
        new = FakeConn(f"opened-{len(opened)}")
        opened.append(new)
        return new

    wrapper = ResilientPostgresSaver(
        dsn=dsn, conn=conn, saver_factory=FakeSaver, connector=connector
    )
    return wrapper, opened


# ── the predicate that decides everything ───────────────────────────────────


def test_connection_is_dead_treats_none_as_dead():
    # aclose() nulls the connection. A liveness check that dereferenced it
    # would raise AttributeError instead of reconnecting.
    assert connection_is_dead(None) is True


def test_connection_is_dead_on_a_live_connection():
    assert connection_is_dead(FakeConn()) is False


def test_connection_is_dead_when_closed():
    # pgbouncer's clean termination path.
    conn = FakeConn()
    conn.closed = True
    assert connection_is_dead(conn) is True


def test_connection_is_dead_when_broken_but_not_closed():
    # The exact state the production traceback left behind: the socket died
    # mid-protocol, so the connection is `broken` but not `closed`. Checking
    # only `.closed` would miss it and the wrapper would never reconnect.
    conn = FakeConn()
    conn.broken = True
    assert connection_is_dead(conn) is True


# ── the wrapper is transparent to the Pregel loop ───────────────────────────


def test_forwards_every_method_the_pregel_loop_calls():
    # If upstream renames or removes one of these, a silent __getattr__ fallback
    # would turn into a 500 on the first stream. Named explicitly so it breaks
    # here instead.
    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

    missing = [m for m in FORWARDED_METHODS if not hasattr(AsyncPostgresSaver, m)]
    assert missing == [], f"wrapper forwards methods AsyncPostgresSaver lacks: {missing}"


def test_forwarded_methods_are_all_coroutines():
    not_coro = [
        m
        for m in FORWARDED_METHODS
        if not inspect.iscoroutinefunction(getattr(ResilientPostgresSaver, m))
    ]
    assert not_coro == [], f"forwarded methods must be awaitable: {not_coro}"


async def test_aget_tuple_passes_arguments_through():
    wrapper, _ = _build(FakeConn())
    result = await wrapper.aget_tuple({"configurable": {"thread_id": "t1"}})
    assert result == {"thread_id": "t1"}
    assert FakeSaver.instances[0].calls == ["aget_tuple"]


# ── reconnect: the connection is already dead before the call ───────────────


async def test_reconnects_when_connection_closed_before_use():
    dead = FakeConn("original")
    dead.closed = True
    wrapper, opened = _build(dead)

    await wrapper.aget_tuple({"configurable": {"thread_id": "t2"}})

    assert wrapper.reconnect_count == 1
    # A new saver was built around a new connection.
    assert len(FakeSaver.instances) == 2
    assert len(opened) == 1
    assert FakeSaver.instances[1].conn is opened[0]
    assert opened[0].label != "original"


async def test_reconnects_when_connection_broken_before_use():
    dead = FakeConn("original")
    dead.broken = True
    wrapper, opened = _build(dead)

    await wrapper.aget_tuple({"configurable": {"thread_id": "t3"}})

    assert wrapper.reconnect_count == 1
    assert len(FakeSaver.instances) == 2
    assert FakeSaver.instances[1].conn is opened[0]


async def test_closes_the_dead_connection_it_replaces():
    # Leaking a socket per reconnect would exhaust the pooler's connection
    # limit over a long-running process, turning a one-off failure into a
    # permanent outage.
    dead = FakeConn("original")
    dead.closed = True
    wrapper, _ = _build(dead)

    await wrapper.aget_tuple({"configurable": {"thread_id": "t4"}})

    assert dead.close_calls == 1


async def test_does_not_reconnect_while_the_connection_is_healthy():
    wrapper, opened = _build(FakeConn())

    for i in range(5):
        await wrapper.aget_tuple({"configurable": {"thread_id": f"t{i}"}})

    # One saver, one connection, no churn. This is the common path and it must
    # not pay for the resilience.
    assert wrapper.reconnect_count == 0
    assert opened == []
    assert len(FakeSaver.instances) == 1
    assert FakeSaver.instances[0].calls == ["aget_tuple"] * 5


async def test_recovers_from_the_production_failure_shape():
    # The exact traceback, and the exact path it took. The liveness pre-check
    # PASSES here -- psycopg cannot know a socket is gone until it reads from
    # it -- so this is the retry branch, not the pre-check branch. That is why
    # the real incident survived a "is the connection alive?" guard: the guard
    # is necessary but not sufficient.
    wrapper, _ = _build(FakeConn("original"))
    wrapper._saver.raise_next = OperationalError(
        "consuming input failed: server closed the connection unexpectedly"
    )
    wrapper._saver.kill_conn_on_raise = True

    result = await wrapper.aget_tuple({"configurable": {"thread_id": "prod"}})

    assert result == {"thread_id": "prod"}
    assert wrapper.retry_count == 1
    assert wrapper.reconnect_count == 1


# ── reconnect: the connection dies mid-operation ────────────────────────────


async def test_retries_once_when_connection_dies_during_the_call():
    # The race the pre-check cannot cover, stated generically: the socket is
    # reaped between the liveness check and the query, so the first attempt
    # raises even though the connection looked alive a moment earlier.
    wrapper, _ = _build(FakeConn("original"))
    wrapper._saver.raise_next = OperationalError("terminating connection")
    wrapper._saver.kill_conn_on_raise = True

    result = await wrapper.aget_tuple({"configurable": {"thread_id": "race"}})

    assert result == {"thread_id": "race"}
    assert wrapper.retry_count == 1
    assert wrapper.reconnect_count == 1
    assert len(FakeSaver.instances) == 2


async def test_does_not_retry_a_healthy_connection_raising_operational_error():
    # psycopg raises OperationalError for real server-side faults too (statement
    # timeout, protocol failure). Retrying those would turn a genuine fault into
    # a duplicate write, so a live connection must re-raise untouched -- which
    # is exactly why the wrapper demands the connection confirm it is dead.
    wrapper, _ = _build(FakeConn("healthy"))
    wrapper._saver.raise_next = OperationalError(
        " canceling statement due to statement timeout"
    )
    wrapper._saver.kill_conn_on_raise = False

    with pytest.raises(OperationalError, match="statement timeout"):
        await wrapper.aget_tuple({"configurable": {"thread_id": "timeout"}})

    assert wrapper.retry_count == 0
    assert wrapper.reconnect_count == 0
    assert len(FakeSaver.instances) == 1


async def test_propagates_a_second_failure_after_reconnecting():
    # One retry, not a loop. If the replacement connection also fails, that is
    # a real outage and must surface rather than retry forever.
    wrapper, _ = _build(FakeConn("original"))
    wrapper._saver.raise_next = OperationalError("terminating connection")
    wrapper._saver.kill_conn_on_raise = True
    # Arm the replacement saver too, after the swap, via the factory hook.
    original_factory = wrapper._saver_factory

    def factory(conn):
        saver = original_factory(conn)
        if len(FakeSaver.instances) == 2:
            saver.raise_next = OperationalError("terminating connection")
            saver.kill_conn_on_raise = True
        return saver

    wrapper._saver_factory = factory

    with pytest.raises(OperationalError):
        await wrapper.aget_tuple({"configurable": {"thread_id": "double"}})

    assert wrapper.retry_count == 1


async def test_non_operational_errors_are_not_retried():
    # A bug in a query or a schema mismatch must not be masked by a retry.
    wrapper, _ = _build(FakeConn())
    wrapper._saver.raise_next = ValueError("column does not exist")

    with pytest.raises(ValueError, match="does not exist"):
        await wrapper.aput({"configurable": {"thread_id": "x"}}, {}, {}, {})

    assert wrapper.retry_count == 0


# ── shutdown ────────────────────────────────────────────────────────────────


async def test_aclose_closes_the_connection():
    conn = FakeConn()
    wrapper, _ = _build(conn)

    await wrapper.aclose()

    assert conn.close_calls == 1
    assert wrapper._conn is None


async def test_aclose_is_idempotent():
    wrapper, _ = _build(FakeConn())

    await wrapper.aclose()
    await wrapper.aclose()  # must not raise on the already-closed connection

    assert wrapper._conn is None


async def test_aclose_swallows_close_errors():
    # Shutdown must not raise because a dead socket failed to close politely.

    class ExplodingConn(FakeConn):
        async def close(self):
            self.close_calls += 1
            raise RuntimeError("socket already gone")

    wrapper, _ = _build(ExplodingConn())

    await wrapper.aclose()

    assert wrapper._conn is None


async def test_calling_an_operation_after_close_raises_clearly():
    # Better a named error at shutdown than an AttributeError on None -- and
    # better still than silently reconnecting, which would resurrect the
    # checkpointer after shutdown and leak a connection past process exit.
    wrapper, opened = _build(FakeConn())
    await wrapper.aclose()

    with pytest.raises(RuntimeError, match="closed"):
        await wrapper.aget_tuple({"configurable": {"thread_id": "z"}})

    # The important half: it did not quietly open a new connection.
    assert opened == []


# ── concurrency ─────────────────────────────────────────────────────────────


async def test_concurrent_calls_after_the_connection_dies_reconnect_once():
    # Ten streams in flight when the socket dies. They must all succeed, and
    # they must not each open their own connection — a thundering herd of
    # reconnects against a struggling database is its own outage.
    dead = FakeConn("original")
    dead.closed = True
    wrapper, opened = _build(dead)

    results = await asyncio.gather(
        *(
            wrapper.aget_tuple({"configurable": {"thread_id": f"c{i}"}})
            for i in range(10)
        )
    )

    assert len(results) == 10
    assert {r["thread_id"] for r in results} == {f"c{i}" for i in range(10)}
    # The lock plus the re-check inside it means one replacement, not ten.
    assert wrapper.reconnect_count == 1
    assert len(opened) == 1


async def test_concurrent_mid_operation_failures_are_bounded():
    # Ten streams in flight when the transport dies under them. Every task must
    # get a result, and the total number of replacement connections must stay
    # bounded -- a thundering herd of reconnects against a struggling database
    # is its own outage.
    #
    # Note what is NOT asserted: that all ten hit the retry branch. The fake
    # fails once, like a real single transport failure, so whichever task
    # reconnected first repairs the connection for the other nine and they
    # never see an error. That is the desired outcome, not a shortfall.
    original = FakeConn("original")
    wrapper, opened = _build(original)
    wrapper._saver.raise_next = OperationalError("terminating connection")
    wrapper._saver.kill_conn_on_raise = True

    async def one(i):
        return await wrapper.aget_tuple({"configurable": {"thread_id": f"h{i}"}})

    results = await asyncio.gather(*(one(i) for i in range(10)))

    assert len(results) == 10
    assert {r["thread_id"] for r in results} == {f"h{i}" for i in range(10)}
    # One failure, so one retry -- and the fix it bought applies to everyone.
    assert wrapper.retry_count == 1
    # Bounded: at most one replacement per failure, never ten.
    assert wrapper.reconnect_count == 1
    assert len(opened) == 1
    # And the dead socket was released exactly once, not leaked per task.
    assert original.close_calls == 1
