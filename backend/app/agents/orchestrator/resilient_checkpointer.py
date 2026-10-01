"""
Reconnecting wrapper around LangGraph's ``AsyncPostgresSaver``.

The problem this exists to solve
─────────────────────────────────
``AsyncPostgresSaver`` is bound to a single psycopg ``AsyncConnection`` and
exposes no pool API, so ``lifespan_graph`` opens one connection and holds it
for the entire process lifetime. That is fine against a directly-reachable
Postgres, and wrong against a transaction-mode pooler (Supabase's port 6543,
i.e. pgbouncer), which is free to close the server-side connection on its own
idle/lifetime timer.

When that happens the Python object is still alive and still looks valid. The
next chat message touches the checkpointer on its *first* read
(``aget_tuple``, from ``AsyncPregelLoop.__aenter__``) and dies in
milliseconds with::

    psycopg.OperationalError: consuming input failed:
    server closed the connection unexpectedly

A dead transport, not a query error — so retries, timeouts and query tuning
cannot help. The fix has to be able to *replace* the connection underneath the
already-compiled graph, which is why this is a wrapper rather than a reconnect
call in the stream handler: the graph captures the checkpointer object at
``compile()`` time and never looks it up again.

What it does
────────────
Before every operation it checks the underlying connection's ``closed`` and
``broken`` flags, and replaces the connection if it is gone. It also retries
once if the connection dies *during* an operation — that race is unavoidable,
because a socket can be reaped between the liveness check and the query.

Both halves are needed. The pre-check avoids the error entirely in the common
case; the retry covers dying mid-flight, which a network blip can cause just
as easily as a pooler timer.

Concurrency note: swapping the connection is serialised on a lock, but a task
already awaiting an operation on the old connection is not held back from it.
That is deliberate. ``AsyncPostgresSaver`` serialises its own operations on
the single connection, so at most one task is mid-query at a time, and that
query is lost either way — the alternative (reference-counting in-flight
operations and deferring the swap) buys nothing here, because a cancelled
checkpoint write has already failed.

The wrapper is deliberately transparent: it exposes the async methods
LangGraph's Pregel loop actually calls, and records counters so reconnects are
observable in logs rather than silent.

Why it subclasses ``BaseCheckpointSaver``
─────────────────────────────────────────
It has to. LangGraph validates a checkpointer with ``isinstance(x,
BaseCheckpointSaver)`` while compiling a graph, and the ``except TypeError`` in
``lifespan_graph`` turns a rejection into a warning plus an ``InMemorySaver``
fallback. A duck-typed wrapper with every right method therefore compiles
happily, raises ``TypeError`` at the one moment it matters, and is discarded --
leaving the graph with no persistence while every test in
``test_resilient_checkpointer.py`` still passed, because those tests exercise
the wrapper directly and never ask LangGraph whether it accepts it. Subclassing
is not the fix for a hypothetical; it is the difference between a checkpointer
and an object that looks like one.
"""
from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

import structlog
from langgraph.checkpoint.base import BaseCheckpointSaver
from psycopg import AsyncConnection, OperationalError
from psycopg.rows import dict_row

logger = structlog.get_logger(__name__)

# The AsyncPostgresSaver methods the Pregel loop calls. Kept explicit rather
# than delegated through __getattr__ so a renamed or removed upstream method
# fails loudly here (AttributeError at import of this module's setup step)
# instead of silently becoming a 500 on the first stream. `setup` is included
# because lifespan_graph creates the checkpoint tables through it.
#
# The list is the *whole* async surface of BaseCheckpointSaver, not just the
# methods the loop happens to call today. Once this class subclasses the base --
# which it must, see below -- any method it does not define inherits the base's
# NotImplementedError stub, so an incomplete list would trade one silent failure
# for a louder one. `test_every_forwarded_method_exists_on_the_real_saver`
# checks the names against the installed AsyncPostgresSaver, so this cannot rot
# against a LangGraph upgrade without a test going red.
FORWARDED_METHODS = (
    "setup",
    "aget",
    "aget_tuple",
    "alist",
    "aput",
    "aput_writes",
    "adelete_thread",
    "adelete_for_runs",
    "acopy_thread",
    "aprune",
    "aget_delta_channel_history",
)


def connection_is_dead(conn: AsyncConnection | None) -> bool:
    """
    True when the connection cannot be used for another query.

    ``closed`` means it was closed cleanly (our own aclose, or the server sent
    a clean termination). ``broken`` means the protocol layer is unusable
    without an explicit reset — which is the state a pgbouncer-induced socket
    reap leaves behind, and the one that produced the production traceback.
    """
    if conn is None:
        return True
    return bool(conn.closed or conn.broken)


class ResilientPostgresSaver(BaseCheckpointSaver):
    """
    A checkpointer that transparently replaces its connection when it dies.

    Construct with an already-open ``AsyncConnection``; ``aclose()`` releases
    it. Reconnects create their own connections, which ``aclose()`` also
    releases.

    The ``BaseCheckpointSaver`` base is not decoration. LangGraph validates a
    checkpointer with ``isinstance(x, BaseCheckpointSaver)`` before compiling a
    graph, and this module's own ``lifespan_graph`` catches the resulting
    ``TypeError`` and falls back to ``InMemorySaver``. So a plain class with the
    right methods is not a checkpointer that reconnects -- it is a checkpointer
    that is silently discarded, leaving the graph with no persistence at all and
    the reconnect logic below never once executed in production.
    """

    def __init__(
        self,
        dsn: str,
        conn: AsyncConnection,
        saver_factory: Callable[..., Any],
        connector: Callable[..., Awaitable[Any]] | None = None,
    ) -> None:
        super().__init__()
        self._dsn = dsn
        self._conn: AsyncConnection | None = conn
        self._saver_factory = saver_factory
        self._saver: Any = saver_factory(conn=conn)
        self._lock = asyncio.Lock()
        # `self.serde` comes from BaseCheckpointSaver.__init__ and is the same
        # JsonPlusSerializer AsyncPostgresSaver defaults to. Left alone rather
        # than copied off the inner saver: the inner one is built with no serde
        # argument, so a copy would be indistinguishable from the default and
        # would only add a coupling to test fakes.
        # Injectable so tests can exercise reconnection without a live database.
        # Defaults to the real driver; production never passes this.
        self._connector = connector or AsyncConnection.connect
        self.reconnect_count = 0
        self.retry_count = 0
        # Set by aclose(). Without it, a post-shutdown call would see a None
        # connection, decide it was dead, and cheerfully reconnect -- turning a
        # clean shutdown into a live one that leaks.
        self._closed = False

    # ── connection lifecycle ──────────────────────────────────────────────────

    async def _open(self) -> Any:
        """
        Open a fresh connection.

        Mirrors the arguments used at startup in ``lifespan_graph``:
        autocommit, ``dict_row`` rows, and ``prepare_threshold=None``. That last
        one is not cosmetic — it disables server-side PREPARE, which collides
        with live prepared statements on a backend session reused after a
        pooler-assigned restart.
        """
        return await self._connector(
            self._dsn,
            autocommit=True,
            prepare_threshold=None,
            row_factory=dict_row,
        )

    async def _replace_connection(self, reason: str) -> None:
        """
        Swap in a fresh connection. Caller must hold ``self._lock``.

        The dead connection is closed first, and if closing it raises that is
        swallowed — it is already unusable, and failing here would mask the
        reconnect we are trying to make.
        """
        stale, self._conn = self._conn, None
        if stale is not None:
            try:
                await stale.close()
            except Exception as close_exc:
                # Best effort, but logged rather than dropped. The connection is
                # already unusable so a close() failure does not stop the
                # reconnect -- but a driver that cannot release a socket is a
                # leak, and a silent `pass` here would hide it until the
                # database refuses new connections.
                logger.debug(
                    "langgraph_checkpointer_stale_close_failed",
                    error=str(close_exc)[:200],
                )
        self._conn = await self._open()
        self._saver = self._saver_factory(conn=self._conn)
        self.reconnect_count += 1
        logger.warning(
            "langgraph_checkpointer_reconnected",
            reconnect_count=self.reconnect_count,
            reason=reason,
            hint=(
                "The connection backing the LangGraph checkpointer was closed by "
                "the server. If this repeats, set LANGGRAPH_CHECKPOINT_DSN to a "
                "session-mode endpoint (for Supabase: same host, port 5432 "
                "instead of the transaction-pooler port 6543)."
            ),
        )

    async def _ensure_live(self) -> None:
        """Replace the connection if the current one is closed or broken."""
        if self._closed:
            # A closed wrapper is a finished wrapper. Reconnecting here would
            # resurrect the checkpointer after shutdown, and the new connection
            # would then outlive the process.
            raise RuntimeError("checkpointer is closed")
        if not connection_is_dead(self._conn):
            return
        async with self._lock:
            # Re-check under the lock: another task may have replaced the
            # connection while this one waited, and reconnecting again would
            # leak the fresh one it just opened.
            if not connection_is_dead(self._conn):
                return
            await self._replace_connection("dead_before_use")

    async def aclose(self) -> None:
        """Close the current connection, if open. Never raises."""
        async with self._lock:
            self._closed = True
            conn, self._conn = self._conn, None
            self._saver = None
        if conn is None or conn.closed:
            return
        try:
            await conn.close()
        except Exception as exc:  # pragma: no cover - shutdown best effort
            logger.warning("langgraph_checkpointer_close_failed", error=str(exc))

    # ── operation forwarding ──────────────────────────────────────────────────

    async def call(self, name: str, *args: Any, **kwargs: Any) -> Any:
        """
        Run one saver operation, reconnecting and retrying once if the
        connection died.

        An ``OperationalError`` on a connection that still reports itself usable
        is re-raised untouched: psycopg uses that class for genuine server-side
        errors too (a statement timeout, a protocol-level failure), and silently
        retrying those would turn a real fault into a duplicate write.
        """
        await self._ensure_live()
        try:
            return await self._invoke(name, *args, **kwargs)
        except OperationalError as exc:
            if not connection_is_dead(self._conn):
                raise
            logger.warning(
                "langgraph_checkpointer_operation_retrying",
                operation=name,
                reconnect_count=self.reconnect_count,
                error=str(exc)[:200],
            )
            self.retry_count += 1
            async with self._lock:
                # Only reconnect if nobody else already did while we waited.
                if connection_is_dead(self._conn):
                    await self._replace_connection("dead_during_operation")
            return await self._invoke(name, *args, **kwargs)

    async def _invoke(self, name: str, *args: Any, **kwargs: Any) -> Any:
        if self._saver is None:
            raise RuntimeError("checkpointer is closed")
        result: Awaitable[Any] | Any = getattr(self._saver, name)(*args, **kwargs)
        return await result


def _make_forwarder(name: str) -> Callable[..., Awaitable[Any]]:
    async def _forward(self: ResilientPostgresSaver, *args: Any, **kwargs: Any) -> Any:
        return await self.call(name, *args, **kwargs)

    _forward.__name__ = name
    _forward.__qualname__ = f"ResilientPostgresSaver.{name}"
    _forward.__doc__ = f"Reconnecting wrapper for AsyncPostgresSaver.{name}()."
    return _forward


for _name in FORWARDED_METHODS:
    setattr(ResilientPostgresSaver, _name, _make_forwarder(_name))
del _name


__all__ = ["ResilientPostgresSaver", "FORWARDED_METHODS", "connection_is_dead"]
