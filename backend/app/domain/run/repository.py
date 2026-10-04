"""
Repository for AgentRun / RunEvent.

Two things here are not generic CRUD and are therefore written explicitly rather
than reached for through ``BaseRepository``:

* ``add_events`` — a batch insert in one transaction. A per-frame
  ``add`` + ``commit`` would be one network round-trip per token delta against a
  hosted pooler, which costs more than the model call that produced it.
* ``finish_run`` — ``BaseRepository.update`` skips ``None`` values, so it
  literally cannot clear ``error`` or leave ``message_id`` unset. A finish that
  silently failed to clear its error would be reported to the next reader as a
  run that failed after it succeeded.
"""
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from backend.app.domain.base_repository import BaseRepository
from backend.app.domain.run.models import AgentRun, RunEvent
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

# Bound on a single replay read. A pathological run cannot make a reconnecting
# client buffer an unbounded response: the tailer loops and re-reads.
REPLAY_PAGE_SIZE = 500


class RunRepository(BaseRepository[AgentRun]):
    def __init__(self, session: AsyncSession):
        super().__init__(session, AgentRun)

    # ── runs ──────────────────────────────────────────────────────────────

    async def create_run(self, **fields: Any) -> AgentRun:
        run = AgentRun(**fields)
        self.session.add(run)
        await self.session.commit()
        await self.session.refresh(run)
        return run

    async def get_run(self, run_id: UUID) -> AgentRun | None:
        result = await self.session.exec(select(AgentRun).where(AgentRun.id == run_id))
        return result.first()

    async def get_run_for_user(self, run_id: UUID, user_id: UUID) -> AgentRun | None:
        """Fetch a run *scoped to its owner*.

        This is the IDOR guard for the rejoin endpoint, and it filters on
        ``user_id`` in the same statement rather than fetching then comparing:
        a fetch-then-compare leaks existence to a caller who guessed a valid
        run id belonging to someone else, because the two cases would return
        different responses only after the row had been read.
        """
        result = await self.session.exec(
            select(AgentRun).where(AgentRun.id == run_id, AgentRun.user_id == user_id)
        )
        return result.first()

    async def latest_run_for_conversation(
        self, conversation_id: UUID, user_id: UUID
    ) -> AgentRun | None:
        result = await self.session.exec(
            select(AgentRun)
            .where(
                AgentRun.conversation_id == conversation_id,
                AgentRun.user_id == user_id,
            )
            # `.desc()`/`.asc()` below are the same idiom every repository in
            # `app/domain/` uses, and they raise the same two mypy errors there:
            # SQLModel types a column as its Python type, so statically
            # `created_at` is a `datetime` and `seq` an `int`, and neither has the
            # ordering methods. Suppressed rather than rewritten as
            # `sqlalchemy.desc(...)` because consistency with the nine other
            # repositories is worth more than silencing two diagnostics that are
            # not about this code -- and it is what lets this module go on the
            # mypy allowlist, where the rest of its queries are then checked.
            .order_by(AgentRun.created_at.desc())  # type: ignore[attr-defined]
            .limit(1)
        )
        return result.first()

    async def finish_run(
        self,
        run_id: UUID,
        *,
        status: str,
        message_id: UUID | None = None,
        error: str | None = None,
        event_count: int = 0,
    ) -> AgentRun | None:
        """Mark a run terminal.

        Called only after the final event batch has been committed. A client
        polling ``status`` to decide whether the stream is over would otherwise
        see ``completed`` while the last frames were still unflushed, and would
        end its read missing the end of the answer.
        """
        run = await self.get_run(run_id)
        if run is None:
            return None
        run.status = status
        run.message_id = message_id
        run.error = error
        run.event_count = event_count
        run.finished_at = datetime.now(UTC).replace(tzinfo=None)
        run.updated_at = run.finished_at
        self.session.add(run)
        await self.session.commit()
        await self.session.refresh(run)
        return run

    # ── events ────────────────────────────────────────────────────────────

    async def add_events(
        self, run_id: UUID, rows: list[tuple[int, dict[str, Any]]]
    ) -> int:
        """Insert ``(seq, payload)`` pairs for one run in a single transaction.

        ``run_id`` is a parameter rather than instance state on purpose. The
        writer batches across calls and two runs can be in flight at once, so a
        "current run" attribute would be a silent cross-run write the first time
        they interleaved.
        """
        if not rows:
            return 0
        self.session.add_all(
            [RunEvent(run_id=run_id, seq=seq, payload=payload) for seq, payload in rows]
        )
        await self.session.commit()
        return len(rows)

    async def events_after(
        self, run_id: UUID, after_seq: int, limit: int = REPLAY_PAGE_SIZE
    ) -> list[RunEvent]:
        result = await self.session.exec(
            select(RunEvent)
            .where(RunEvent.run_id == run_id, RunEvent.seq > after_seq)
            .order_by(RunEvent.seq.asc())  # type: ignore[attr-defined]
            .limit(limit)
        )
        return list(result.all())
