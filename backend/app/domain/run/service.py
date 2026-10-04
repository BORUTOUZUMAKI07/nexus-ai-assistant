"""
Run Domain Service.

Thin facade over ``RunRepository``. It exists so the router and the executor
depend on one collaborator rather than reaching into the repository directly, and
so the ownership check has exactly one implementation: ``get_for_user`` is the
only way a run is read from a request, which makes it the only place an IDOR
guard can be forgotten.
"""
from typing import Any
from uuid import UUID

from backend.app.domain.run.models import AgentRun, RunEvent
from backend.app.domain.run.repository import RunRepository
from sqlmodel.ext.asyncio.session import AsyncSession


class RunService:
    def __init__(self, session: AsyncSession):
        self._repo = RunRepository(session)

    async def start_run(
        self,
        *,
        conversation_id: UUID,
        user_id: UUID,
        thread_id: str,
        mode: str,
    ) -> AgentRun:
        return await self._repo.create_run(
            conversation_id=conversation_id,
            user_id=user_id,
            thread_id=thread_id,
            mode=mode,
            status="running",
        )

    async def get_for_user(self, run_id: UUID, user_id: UUID) -> AgentRun | None:
        return await self._repo.get_run_for_user(run_id, user_id)

    async def latest_for_conversation(
        self, conversation_id: UUID, user_id: UUID
    ) -> AgentRun | None:
        return await self._repo.latest_run_for_conversation(conversation_id, user_id)

    async def append_events(
        self, run_id: UUID, rows: list[tuple[int, dict[str, Any]]]
    ) -> int:
        return await self._repo.add_events(run_id, rows)

    async def events_after(
        self, run_id: UUID, after_seq: int, limit: int | None = None
    ) -> list[RunEvent]:
        if limit is None:
            return await self._repo.events_after(run_id, after_seq)
        return await self._repo.events_after(run_id, after_seq, limit=limit)

    async def finish(
        self,
        run_id: UUID,
        *,
        status: str,
        message_id: UUID | None = None,
        error: str | None = None,
        event_count: int = 0,
    ) -> AgentRun | None:
        return await self._repo.finish_run(
            run_id,
            status=status,
            message_id=message_id,
            error=error,
            event_count=event_count,
        )
