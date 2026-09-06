"""
Evaluation & Red-Teaming API Router (admin-only).
Exposes the evaluation service suite — RAGAS, DeepEval, Quality, and the
RedTeam probe battery — as on-demand endpoints persisted into evaluation_logs.
"""
from typing import Any
from uuid import UUID, uuid4

import structlog
from backend.app.api.deps import get_current_admin, get_db
from backend.app.domain.usage.schemas import EvaluationLogCreate
from backend.app.domain.user.models import User
from backend.app.services.evaluation.deepeval_service import deepeval_service
from backend.app.services.evaluation.quality_service import quality_service
from backend.app.services.evaluation.ragas_service import ragas_service
from backend.app.services.evaluation.redteam_service import redteam_service
from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlmodel.ext.asyncio.session import AsyncSession

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/admin/evaluation", tags=["admin-evaluation"])


class RAGTurnRequest(BaseModel):
    question: str = Field(min_length=1)
    answer: str = Field(min_length=1)
    contexts: list[str] = Field(default_factory=list)
    ground_truth: str | None = None


class DeepEvalRequest(BaseModel):
    query: str = Field(min_length=1)
    actual_output: str = Field(min_length=1)
    retrieval_context: list[str] = Field(default_factory=list)


class QualityRequest(BaseModel):
    prompt: str = ""
    response: str = Field(min_length=1)
    tokens_used: int = 0
    latency_ms: float = 0.0


async def _persist_evaluation_logs(
    session: AsyncSession,
    results: list[dict[str, Any]],
    *,
    trace_id: str,
    conversation_id: UUID | None,
    evaluator: str,
) -> None:
    """Writes each scored metric row into evaluation_logs."""
    from backend.app.domain.usage.repository import UsageRepository

    repo = UsageRepository(session)
    for r in results:
        await repo.log_evaluation(
            EvaluationLogCreate(
                trace_id=trace_id,
                conversation_id=conversation_id,
                metric_name=r.get("metric", "unknown"),
                score=float(r.get("score", 0.0)),
                passed=bool(r.get("passed", True)),
                reason=r.get("reason"),
                evaluator=evaluator,
            )
        )


@router.post("/rag")
async def run_ragas_evaluation(
    body: RAGTurnRequest,
    session: AsyncSession = Depends(get_db),
    admin: User = Depends(get_current_admin),
) -> dict[str, Any]:
    """RAGAS-style LLM-as-judge scores for a RAG turn, persisted as evaluation logs."""
    scores = await ragas_service.evaluate_rag_turn(
        question=body.question,
        answer=body.answer,
        contexts=body.contexts,
        ground_truth=body.ground_truth,
    )
    score_rows = [
        {"metric": k, "score": v, "passed": v >= 0.7, "reason": None}
        for k, v in scores.items()
        if k != "overall_score"
    ]
    await _persist_evaluation_logs(
        session,
        score_rows,
        trace_id=str(uuid4()),
        conversation_id=None,
        evaluator="ragas",
    )
    return {"scores": scores, "evaluator": "ragas"}


@router.post("/deepeval")
async def run_deepeval(
    body: DeepEvalRequest,
    session: AsyncSession = Depends(get_db),
    admin: User = Depends(get_current_admin),
) -> dict[str, Any]:
    """DeepEval faithfulness + relevancy for a turn, persisted as evaluation logs."""
    results = await deepeval_service.evaluate_rag_turn(
        query=body.query,
        actual_output=body.actual_output,
        retrieval_context=body.retrieval_context,
    )
    rows = [r.to_dict() for r in results]
    await _persist_evaluation_logs(
        session,
        rows,
        trace_id=str(uuid4()),
        conversation_id=None,
        evaluator="deepeval",
    )
    return {"results": rows, "evaluator": "deepeval"}


@router.post("/quality")
async def run_quality_eval(
    body: QualityRequest,
    admin: User = Depends(get_current_admin),
) -> dict[str, Any]:
    """Deterministic output-quality metrics (tokens/sec, wording efficiency, formatting)."""
    metrics = quality_service.evaluate_response_quality(
        body.prompt, body.response, body.tokens_used, body.latency_ms
    )
    return {"metrics": metrics, "evaluator": "quality"}


@router.post("/redteam")
async def run_redteam_probes(
    admin: User = Depends(get_current_admin),
) -> dict[str, Any]:
    """Runs the adversarial probe battery against the input guardrails."""
    suite = await redteam_service.run_probe_suite()
    return {"report": suite, "evaluator": "redteam"}
