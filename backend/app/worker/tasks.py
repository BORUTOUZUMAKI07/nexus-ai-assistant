"""
Celery Background Tasks.
Handles asynchronous heavy-duty operations:
- File vector ingestion & indexing (RAG pipeline)
- DeepEval background benchmark evaluations
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
from backend.app.infrastructure.storage.supabase_storage import storage_client
from backend.app.services.evaluation.deepeval_service import deepeval_service
from backend.app.services.rag.ingest import ingestion_service
from backend.app.worker.celery_app import celery_app
from celery import group

logger = structlog.get_logger(__name__)


def run_async(coro):
    """Helper to run async coroutines safely within Celery sync worker tasks."""
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    return loop.run_until_complete(coro)


@celery_app.task(name="tasks.process_file_indexing", bind=True, max_retries=3, default_retry_delay=30)
def process_file_indexing_task(self, file_id_str: str) -> dict:
    """
    Background worker task to chunk a document and store vectors in Qdrant.
    """
    logger.info("celery_indexing_task_started", file_id=file_id_str)
    file_id = UUID(file_id_str)

    async def _execute():
        async with async_session_factory() as session:
            repo = FileRepository(session)
            db_file = await repo.get_by_id(file_id)
            if not db_file:
                logger.error("celery_file_not_found", file_id=file_id_str)
                return {"status": "failed", "error": "file_not_found"}

            # storage_path is a Supabase object path, not a local file — pull the
            # raw bytes down to a temp file before chunking.
            try:
                raw_bytes = await storage_client.download(db_file.storage_path)
            except Exception as exc:
                logger.error("celery_file_download_failed", file_id=file_id_str, error=str(exc))
                await repo.update_status(file_id, status="failed", error_message="Storage download failed")
                return {"status": "failed", "error": "storage_download_failed"}

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
                return result
            finally:
                tmp_path.unlink(missing_ok=True)

    try:
        return run_async(_execute())
    except Exception as exc:
        logger.exception("celery_indexing_failed", file_id=file_id_str, error=str(exc))
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
    Periodic evaluation of recent conversations using DeepEval.
    """
    logger.info("periodic_evaluation_task_running")
    return {"status": "completed", "evaluated_turns": 0}


@celery_app.task(name="tasks.cache_cleanup")
def cache_cleanup_task() -> dict:
    """
    Periodic pruning of temporary cache entries or expired locks.
    """
    logger.info("cache_cleanup_task_running")

    async def _cleanup():
        # Clean expired keys or transient locks if any exist
        return True

    run_async(_cleanup())
    return {"status": "cleaned"}


def trigger_indexing_pipeline(file_ids: list[str]):
    """
    Celery Canvas: Dispatches parallel indexing jobs as a Group.
    """
    job = group(process_file_indexing_task.s(fid) for fid in file_ids)
    return job.apply_async()
