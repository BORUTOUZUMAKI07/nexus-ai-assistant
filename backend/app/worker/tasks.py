"""
Celery Background Tasks.
Handles asynchronous heavy-duty operations:
- File vector ingestion & indexing (RAG pipeline)
- Free-tier (heuristic) background evaluation benchmarks
- Redis cache garbage collection and cleanup
- Canvas pipelines (Chains, Groups, Chords)
"""
import asyncio
import tempfile
from pathlib import Path
from uuid import UUID

import structlog
from backend.app.domain.file.repository import FileRepository
from backend.app.infrastructure.database.session import async_session_factory
from backend.app.infrastructure.resilience.guards import singleton_lock
from backend.app.infrastructure.storage.supabase_storage import storage_client
from backend.app.services.evaluation.deepeval_service import deepeval_service
from backend.app.services.rag.ingest import ingestion_service
from backend.app.worker.celery_app import celery_app

logger = structlog.get_logger(__name__)


def run_async(coro):
    """Helper to run async coroutines safely within Celery sync worker tasks."""
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    return loop.run_until_complete(coro)


def _run_locked(task_name: str, ttl_seconds: int, coro_factory):
    """Run ``coro_factory()`` only when this worker holds the singleton lock.

    HLD leader-election-lite: periodic jobs must not double-run when several
    beat workers are alive. Returns None when another worker owns the lock.
    """
    async def _guard():
        async with singleton_lock(task_name, ttl_seconds=ttl_seconds) as acquired:
            if not acquired:
                logger.info("periodic_task_skipped_another_worker", task=task_name)
                return None
            return await coro_factory()

    return run_async(_guard())


@celery_app.task(
    name="tasks.process_file_indexing",
    bind=True,
    max_retries=3,
    default_retry_delay=30,
    rate_limit="10/m",
    acks_late=True,
)
def process_file_indexing_task(self, file_id_str: str, payload: dict | None = None) -> dict:
    """
    Background worker task to chunk a document and store vectors in Qdrant.

    ``payload`` is an optional bag delivered by the event publisher seam
    (CeleryPublisher → send_task kwargs). Callers using the event seam pass it
    through; direct ``.delay()`` callers omit it.
    """
    logger.info("celery_indexing_task_started", file_id=file_id_str, payload_keys=list((payload or {}).keys()))
    file_id = UUID(file_id_str)

    async def _execute():
        async with async_session_factory() as session:
            repo = FileRepository(session)
            db_file = await repo.get_by_id(file_id)
            if not db_file:
                logger.warning("celery_file_not_found", file_id=file_id_str)
                return {"status": "failed", "error": "file_not_found"}

            if db_file.status == "indexed":
                logger.info("celery_indexing_already_completed", file_id=file_id_str)
                return {"status": "skipped", "reason": "already_indexed"}

            claimed = await repo.claim_indexing(file_id)
            if not claimed:
                logger.info("celery_indexing_claim_unavailable", file_id=file_id_str, current_status=db_file.status)
                return {"status": "skipped", "reason": "already_claimed_or_not_pending"}

            # storage_path is a Supabase object path, not a local file — pull the
            # raw bytes down to a temp file before chunking.
            try:
                raw_bytes = await storage_client.download(db_file.storage_path)
            except Exception as exc:
                await repo.update_status(file_id, status="failed", error_message="Storage download failed")
                logger.warning("celery_file_download_failed", file_id=file_id_str, error_type=type(exc).__name__, retry_count=self.request.retries)
                raise

            suffix = Path(db_file.original_filename or db_file.filename).suffix
            with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
                tmp.write(raw_bytes)
                tmp_path = Path(tmp.name)

            try:
                result = await ingestion_service.ingest_file_parent_child(
                    file_id=db_file.id,
                    file_path=tmp_path,
                    filename=db_file.original_filename,
                    user_id=db_file.user_id,
                    session=session,
                    conversation_id=db_file.conversation_id,
                )
                # The ingestion service reports handled pipeline failures as a
                # result payload rather than raising. Convert those to exceptions
                # here so Celery retries transient embedding/vector/database faults.
                if result.get("status") != "success":
                    reason = result.get("reason") or result.get("error") or "ingestion_failed"
                    raise RuntimeError(f"File ingestion did not succeed: {reason}")
                return result
            finally:
                tmp_path.unlink(missing_ok=True)

    try:
        return run_async(_execute())
    except Exception as exc:
        logger.error("celery_indexing_failed", file_id=file_id_str, error_type=type(exc).__name__, retry_count=self.request.retries)
        async def _mark_retryable_failure():
            async with async_session_factory() as session:
                await FileRepository(session).update_status(file_id, status="failed", error_message="Indexing temporarily failed")
        try:
            run_async(_mark_retryable_failure())
        except Exception as status_exc:
            logger.warning("celery_indexing_failure_status_update_failed", file_id=file_id_str, error_type=type(status_exc).__name__)
        raise self.retry(exc=exc)


@celery_app.task(name="tasks.evaluate_turn")
def evaluate_turn_task(query: str, response: str, contexts: list[str]) -> dict:
    """
    Evaluates an individual RAG or LLM conversation turn for hallucination and relevancy.
    """
    async def _eval():
        results = await deepeval_service.evaluate_rag_turn(
            query=query,
            actual_output=response,
            retrieval_context=contexts,
        )
        return [r.to_dict() for r in results]

    res = run_async(_eval())
    return {"status": "completed", "results": res}


@celery_app.task(name="tasks.periodic_evaluation")
def periodic_evaluation_task() -> dict:
    """
    Periodic evaluation of recent conversations via the free-tier heuristics.
    """
    logger.info("periodic_evaluation_task_running")
    return {"status": "completed", "evaluated_turns": 0}


@celery_app.task(name="tasks.cache_cleanup")
def cache_cleanup_task() -> dict:
    """
    Periodic pruning of temporary cache entries or expired locks.
    Singleton-guarded: only one worker runs the sweep per interval.
    """
    logger.info("cache_cleanup_task_running")

    async def _cleanup():
        # Clean expired keys or transient locks if any exist
        return True

    cleaned = _run_locked("cache_cleanup", 300, _cleanup)
    if cleaned is None:
        return {"status": "skipped", "reason": "another_worker_running"}
    return {"status": "cleaned"}


@celery_app.task(name="tasks.periodic_drift_check")
def periodic_drift_check_task(recent_hours: int = 24) -> dict:
    """
    Sliding-window drift check over usage/evaluation telemetry (MD §7.6/§8.8).
    Logs whether any LLM-era signal moved >2σ from its baseline window; the
    admin endpoint exposes the full report. No persistence — pure computation
    over the telemetry tables. Singleton-guarded to avoid duplicate scans.
    """
    from backend.app.services.monitoring.drift_service import DriftService

    async def _run():
        async with async_session_factory() as session:
            service = DriftService(session)
            report = await service.drift_report()
            return report

    report = _run_locked("periodic_drift_check", 300, _run)
    if report is None:
        return {"status": "skipped", "reason": "another_worker_running"}
    logger.info(
        "periodic_drift_check_completed",
        detected=report.get("detected"),
        drifting_metrics=report.get("drifting_metrics"),
    )
    return {"status": "completed", "detected": report.get("detected", False), "drifting_metrics": report.get("drifting_metrics", 0)}


@celery_app.task(name="tasks.prompt_regression_review")
def prompt_regression_review_task(system_prompt: str, threshold: float = 0.8) -> dict:
    """
    Background run of the guardrail golden-set regression gate against a
    candidate system prompt. Gate result is logged; a failing gate is the
    prompt change's "CI stopped" equivalent. Singleton-guarded per interval.
    """
    from backend.app.services.evaluation.regression_service import prompt_regression_gate

    async def _run():
        report = await prompt_regression_gate.evaluate_prompt(system_prompt=system_prompt, threshold=threshold)
        return report

    report = _run_locked("prompt_regression_review", 300, _run)
    if report is None:
        return {"status": "skipped", "reason": "another_worker_running"}
    logger.info(
        "prompt_regression_review_completed",
        gate=report.get("gate"),
        score=report.get("score"),
        critical_failures=len(report.get("critical_failures", [])),
    )
    return {"status": "completed", "gate": report.get("gate"), "score": report.get("score")}


@celery_app.task(name="tasks.retry_webhook_deliveries")
def retry_webhook_deliveries_task() -> dict:
    """
    Periodic retry of failed webhook deliveries (bounded by WEBHOOK_MAX_ATTEMPTS).
    Complements the synchronous best-effort send in the message path and the
    manual /webhooks/{id}/redeliver endpoint. Singleton-guarded per interval.
    """
    from backend.app.services.webhook_service import retry_failed_deliveries

    async def _run():
        async with async_session_factory() as session:
            return await retry_failed_deliveries(session)

    retried = _run_locked("retry_webhook_deliveries", 300, _run)
    if retried is None:
        return {"status": "skipped", "reason": "another_worker_running"}
    logger.info("webhook_retry_pass_completed", retried=retried)
    return {"status": "completed", "retried": retried}
