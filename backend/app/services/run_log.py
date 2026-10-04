"""
Durable run log: the broker, the batching writer, and the replay/live tailer.

This is the module that makes "rejoin after a refresh" a real feature instead of
a frontend-only lie (AGENTS.md §9.25). Three pieces, each with one job:

``RunBroker``
    In-process fan-out. Per run, there is exactly **one** writer (the executor),
    which is what makes ``publish`` safe without a lock: concurrent publishers
    for different runs touch different queues, and two writers for one run are
    prevented upstream by the per-thread slot guard.

``RunWriter``
    Assigns ``seq``, publishes to the broker *immediately*, and batches the
    database insert. The split is the whole design: a client sees frames with
    zero added database latency, and the durable copy trails by at most one
    flush interval. A per-frame insert+commit would be one round-trip per token
    delta against a hosted pooler — more expensive than the model call that
    produced the token.

``tail_run``
    Reads what a client missed and then follows the live stream. Two rules make
    it correct rather than merely working:

    1. **A live frame is yielded only when its ``seq`` is exactly the next one
       expected.** Anything else means a gap, and the reader falls back to the
       database to fill it. Yielding live frames unconditionally is the obvious
       implementation and it silently *loses* frames: a broker frame with
       seq 5 arriving before the batched row for seq 4 is on disk advances the
       cursor to 5, and the row for 4 is then skipped as "already seen".
    2. **Termination is ``AgentRun.status``, never the end of a stream.** A
       stream that stops is indistinguishable from a client that vanished, which
       is precisely the failure this module exists to remove.
"""
from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncGenerator, Callable
from typing import Any
from uuid import UUID

import structlog
from backend.app.domain.run.repository import REPLAY_PAGE_SIZE, RunRepository

logger = structlog.get_logger(__name__)

# Queue depth per subscriber. A stalled reader must not be able to grow the
# process's memory without bound; overflow is recoverable because the reader
# falls back to the database (see rule 1 above).
SUBSCRIBER_QUEUE_SIZE = 1000

# Frames buffered before a flush is forced, independent of the timer.
DEFAULT_BATCH_SIZE = 64

# How long a batch may sit unflushed. This is the worst-case delay between a
# frame being emitted and being replayable, and it is why the broker path exists.
DEFAULT_FLUSH_INTERVAL_SECONDS = 0.25

# How long a reader waits for a live frame before re-checking the database.
# Only matters when the broker dropped frames or the reader is between runs.
DEFAULT_POLL_SECONDS = 0.5

# A batch that fails twice is dropped rather than retried forever, so a broken
# database cannot grow the buffer without limit. The cost is a hole in the
# replay, which the terminal `done` frame's full `content` reconciles.
_FLUSH_ATTEMPTS = 2


class _EndOfRun:
    """Sentinel pushed to subscribers when the writer is finished.

    A distinct object rather than ``None``: a frame payload is never ``None``,
    but a sentinel that shares a value with "no value" is one refactor away from
    being indistinguishable from a real frame.
    """

    __slots__ = ()

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return "<END_OF_RUN>"


END_OF_RUN = _EndOfRun()


# One subscriber queue carries frames as ``(seq, frame)`` and the end of the
# run as a distinct sentinel object, so the two have to be told apart. The
# union is the type; ``Queue`` without arguments erases exactly the fact the
# branch in ``tail_run`` depends on.
#
# Declared here rather than with the other module constants because a plain
# alias is evaluated at import and cannot name a class that does not exist yet.
# PEP 695 ``type`` syntax would defer that -- and is a SyntaxError below 3.12,
# which ``requires-python = ">=3.11"`` in pyproject.toml still permits.
SubscriberItem = tuple[int, dict[str, Any]] | _EndOfRun


class RunBroker:
    """Per-run pub/sub of frames to connected readers."""

    def __init__(self) -> None:
        self._subs: dict[UUID, list[asyncio.Queue[SubscriberItem]]] = {}

    def subscribe(self, run_id: UUID) -> asyncio.Queue[SubscriberItem]:
        queue: asyncio.Queue[SubscriberItem] = asyncio.Queue(
            maxsize=SUBSCRIBER_QUEUE_SIZE
        )
        self._subs.setdefault(run_id, []).append(queue)
        return queue

    def unsubscribe(self, run_id: UUID, queue: asyncio.Queue[SubscriberItem]) -> None:
        listeners = self._subs.get(run_id)
        if not listeners:
            return
        try:
            listeners.remove(queue)
        except ValueError:
            # Already gone. Idempotent by design: unsubscribe runs in a finally
            # and a reader can be torn down twice (client disconnect racing
            # terminal status).
            return
        if not listeners:
            self._subs.pop(run_id, None)

    def publish(self, run_id: UUID, seq: int, frame: dict[str, Any]) -> None:
        """Deliver one frame to every current subscriber of ``run_id``.

        Synchronous and lock-free. Safe because a run has exactly one writer
        (the executor) and asyncio.Queue is not thread-safe but is safe to touch
        from the single event loop that both publish and subscribe run on.

        A full queue drops the frame rather than blocking the run. That is the
        right direction: the durable copy is the authority, and a reader that
        missed a live frame detects the gap by ``seq`` and re-reads.
        """
        for queue in list(self._subs.get(run_id, ())):
            try:
                queue.put_nowait((seq, frame))
            except asyncio.QueueFull:
                logger.warning("run_broker_subscriber_overflow", run_id=str(run_id), seq=seq)

    def listener_count(self, run_id: UUID) -> int:
        return len(self._subs.get(run_id, ()))

    def publish_end(self, run_id: UUID) -> None:
        """Tell every current subscriber the run is over.

        Published only after the final batch is committed, so a reader that acts
        on it and then re-reads cannot miss the tail.
        """
        for queue in list(self._subs.get(run_id, ())):
            try:
                queue.put_nowait(END_OF_RUN)
            except asyncio.QueueFull:
                # The reader will fall back to the timeout path, which checks
                # `status` — so a dropped sentinel degrades latency, not
                # correctness.
                logger.warning("run_broker_end_signal_dropped", run_id=str(run_id))


# Process-wide broker. Same single-process assumption as the hook policy registry
# (§9.1): a second uvicorn worker would serve replays from the database but not
# live frames, which is a latency regression, not a correctness one.
run_broker = RunBroker()


class RunWriter:
    """Assigns sequence numbers, publishes live, batches the durable copy."""

    def __init__(
        self,
        *,
        run_id: UUID,
        broker: RunBroker,
        session_factory: Callable[[], Any],
        flush_interval: float = DEFAULT_FLUSH_INTERVAL_SECONDS,
        batch_size: int = DEFAULT_BATCH_SIZE,
    ) -> None:
        self._run_id = run_id
        self._broker = broker
        self._session_factory = session_factory
        self._flush_interval = flush_interval
        self._batch_size = max(1, batch_size)
        self._buffer: list[tuple[int, dict[str, Any]]] = []
        self._next_seq = 1
        self._emitted = 0
        self._persisted = 0
        self._dropped = 0
        self._last_flush = time.monotonic()

    @property
    def emitted(self) -> int:
        return self._emitted

    @property
    def persisted(self) -> int:
        return self._persisted

    @property
    def dropped(self) -> int:
        return self._dropped

    async def emit(self, frame: dict[str, Any]) -> int:
        """Publish one frame and return the sequence number assigned to it.

        ``seq`` is assigned here, at frame *creation*, not at write time. That
        is what lets broker order, database order and sequence order be the same
        order even though the two write paths run at different speeds.
        """
        seq = self._next_seq
        self._next_seq += 1
        self._emitted += 1
        self._broker.publish(self._run_id, seq, frame)
        self._buffer.append((seq, frame))
        await self._maybe_flush()
        return seq

    async def _maybe_flush(self) -> None:
        due_by_size = len(self._buffer) >= self._batch_size
        due_by_time = (time.monotonic() - self._last_flush) >= self._flush_interval
        if due_by_size or due_by_time:
            await self.flush()

    async def flush(self) -> None:
        """Commit everything buffered. Safe to call when the buffer is empty.

        Must be awaited **before** the run is marked terminal: a reader decides
        a run is over from ``status``, so a status that lands ahead of the final
        flush ends the read before the last frames are readable.
        """
        if not self._buffer:
            self._last_flush = time.monotonic()
            return
        rows, self._buffer = self._buffer, []
        self._last_flush = time.monotonic()

        for attempt in range(_FLUSH_ATTEMPTS):
            try:
                async with self._session_factory() as session:
                    await RunRepository(session).add_events(self._run_id, rows)
            except Exception as exc:
                logger.warning(
                    "run_event_flush_failed",
                    run_id=str(self._run_id),
                    frames=len(rows),
                    attempt=attempt + 1,
                    error_type=type(exc).__name__,
                )
                continue
            self._persisted += len(rows)
            return

        # Out of attempts. Drop the batch rather than retry forever: an unbounded
        # retry buffer is a memory leak that only appears when the database is
        # unhealthy, which is exactly when the run is already at risk.
        self._dropped += len(rows)
        logger.warning(
            "run_event_flush_dropped",
            run_id=str(self._run_id),
            frames=len(rows),
            dropped_total=self._dropped,
        )


async def tail_run(
    run_id: UUID,
    *,
    after_seq: int = 0,
    session_factory: Callable[[], Any],
    broker: RunBroker | None = None,
    poll_seconds: float = DEFAULT_POLL_SECONDS,
) -> AsyncGenerator[dict[str, Any], None]:
    """Yield a run's frames in ``seq`` order: what is already durable, then live.

    Ends only once the run is terminal *and* the database has been re-read, so
    the final frames are always included.
    """
    broker = broker or run_broker
    queue = broker.subscribe(run_id)
    state = {"cursor": max(0, after_seq)}

    async def catch_up() -> AsyncGenerator[dict[str, Any], None]:
        """Replay every persisted frame after the cursor, advancing it.

        Pages until exhausted rather than one page per call, so a client joining
        a long-finished run gets the whole answer before it starts waiting for
        frames that will never come.

        Annotated as a generator because it is one. It was declared ``-> None``,
        which is the kind of annotation that type-checks nothing and misleads
        everything that reads it: the four ``async for ... in catch_up()`` calls
        below were all flagged as iterating ``None``, and mypy would have been
        right about the *type* even though the runtime is fine, because an
        async generator ignores its return annotation.
        """
        while True:
            async with session_factory() as session:
                rows = await RunRepository(session).events_after(run_id, state["cursor"])
            if not rows:
                return
            for row in rows:
                state["cursor"] = row.seq
                yield row.payload
            if len(rows) < REPLAY_PAGE_SIZE:
                return

    async def is_terminal(run_id_: UUID) -> bool:
        async with session_factory() as session:
            status = await RunRepository(session).get_run(run_id_)
        # A missing run row is terminal: there is nothing to follow and nothing
        # left to replay. Callers get an empty stream rather than a hang.
        return status is None or status.status != "running"

    catch_up_gen = catch_up()
    # A frame taken off the queue but not yet delivered. Holding it rather than
    # discarding it is what makes "no consumed frame is ever lost" true by
    # construction: without it, a frame whose predecessors were not yet durable
    # is taken, found to be out of order, and thrown away -- recoverable only by
    # the *next* catch-up, which may never come if the run ends first. See the
    # gap branch below.
    pending: tuple[int, dict[str, Any]] | None = None
    try:
        async for frame in catch_up_gen:
            yield frame

        # Checked once up front so a client that joins an already-finished run
        # returns immediately instead of waiting out the first poll interval.
        if await is_terminal(run_id):
            return

        item: SubscriberItem
        while True:
            # A frame already taken off the queue is offered first, and only once
            # the frames before it have arrived -- see the gap branch below.
            if pending is not None and state["cursor"] + 1 == pending[0]:
                item, pending = pending, None
            else:
                try:
                    item = await asyncio.wait_for(queue.get(), timeout=poll_seconds)
                except (TimeoutError, asyncio.TimeoutError):
                    # Idle. Either the broker dropped frames for this reader, or
                    # the executor died without signalling. Both are answered by
                    # re-reading the durable copy and re-checking status. This
                    # wait is also what paces the retry of a pending frame, so a
                    # gap that never closes costs one read per interval rather
                    # than spinning.
                    if await is_terminal(run_id):
                        break
                    async for frame in catch_up():
                        yield frame
                    continue

            if isinstance(item, _EndOfRun):
                break

            seq, frame = item
            # Rule 1: contiguous only. A gap means the frames before this one were
            # not durable when it arrived -- the broker dropped them on their way
            # to this reader. Yielding it now would advance the cursor past them,
            # and the catch-up would then skip them as already seen, so the answer
            # silently loses a chunk.
            if seq != state["cursor"] + 1:
                async for gap_frame in catch_up():
                    yield gap_frame
                # Kept rather than dropped. This frame belongs to this reader, and
                # the executor will not publish it a second time, so the only thing
                # that can still deliver it is this loop.
                pending = item
                continue
            state["cursor"] = seq
            yield frame

        # Terminal (or end-of-run signal): the executor commits its final batch
        # before flipping status and before publishing END_OF_RUN, so this read
        # is what guarantees the tail of the answer is included.
        async for frame in catch_up():
            yield frame
    finally:
        broker.unsubscribe(run_id, queue)
