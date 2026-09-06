"""
Repository for Usage and Telemetry domain operations.
"""
from uuid import UUID

from backend.app.domain.base_repository import BaseRepository
from backend.app.domain.usage.models import EvaluationLog, UsageLog
from backend.app.domain.usage.schemas import (
    EvaluationLogCreate,
    UsageLogCreate,
    UsageSummaryResponse,
)
from sqlmodel import func, select
from sqlmodel.ext.asyncio.session import AsyncSession


class UsageRepository(BaseRepository[UsageLog]):
    def __init__(self, session: AsyncSession):
        super().__init__(session, UsageLog)

    async def log_usage(self, log_in: UsageLogCreate) -> UsageLog:
        db_log = UsageLog(
            user_id=log_in.user_id,
            conversation_id=log_in.conversation_id,
            message_id=log_in.message_id,
            model=log_in.model,
            provider=log_in.provider,
            prompt_tokens=log_in.prompt_tokens,
            completion_tokens=log_in.completion_tokens,
            total_tokens=log_in.prompt_tokens + log_in.completion_tokens,
            cached_tokens=log_in.cached_tokens,
            latency_ms=log_in.latency_ms,
            cost_usd=log_in.cost_usd,
            status=log_in.status,
            error_message=log_in.error_message,
            metadata_json=log_in.metadata_json,
        )
        self.session.add(db_log)
        await self.session.commit()
        await self.session.refresh(db_log)
        return db_log

    async def get_summary(self, user_id: UUID) -> UsageSummaryResponse:
        statement = select(
            func.sum(UsageLog.total_tokens).label("total_tokens"),
            func.sum(UsageLog.prompt_tokens).label("prompt_tokens"),
            func.sum(UsageLog.completion_tokens).label("completion_tokens"),
            func.sum(UsageLog.cached_tokens).label("cached_tokens"),
            func.sum(UsageLog.cost_usd).label("total_cost"),
            func.count(UsageLog.id).label("total_requests"),
            func.avg(UsageLog.latency_ms).label("avg_latency"),
        ).where(UsageLog.user_id == user_id)

        result = await self.session.exec(statement)
        row = result.first()

        if not row or not row[0]:
            return UsageSummaryResponse(
                total_tokens=0,
                prompt_tokens=0,
                completion_tokens=0,
                cached_tokens=0,
                total_cost_usd=0.0,
                total_requests=0,
                average_latency_ms=0.0,
            )

        return UsageSummaryResponse(
            total_tokens=int(row[0] or 0),
            prompt_tokens=int(row[1] or 0),
            completion_tokens=int(row[2] or 0),
            cached_tokens=int(row[3] or 0),
            total_cost_usd=float(row[4] or 0.0),
            total_requests=int(row[5] or 0),
            average_latency_ms=float(row[6] or 0.0),
        )

    async def log_evaluation(self, eval_in: EvaluationLogCreate) -> EvaluationLog:
        db_eval = EvaluationLog(
            trace_id=eval_in.trace_id,
            conversation_id=eval_in.conversation_id,
            message_id=eval_in.message_id,
            metric_name=eval_in.metric_name,
            score=eval_in.score,
            passed=eval_in.passed,
            reason=eval_in.reason,
            evaluator=eval_in.evaluator,
            metadata_json=eval_in.metadata_json,
        )
        self.session.add(db_eval)
        await self.session.commit()
        await self.session.refresh(db_eval)
        return db_eval

    async def get_evaluations(self, conversation_id: UUID | None = None, limit: int = 50) -> list[EvaluationLog]:
        statement = select(EvaluationLog)
        if conversation_id:
            statement = statement.where(EvaluationLog.conversation_id == conversation_id)
        statement = statement.order_by(EvaluationLog.created_at.desc()).limit(limit)
        result = await self.session.exec(statement)
        return list(result.all())
