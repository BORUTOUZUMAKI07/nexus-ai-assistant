"""
Files and RAG API Router.
Pure HTTP transport layer — delegates all file operations to FileService
and all RAG search operations to RAGService (SRP + DIP).
"""
from uuid import UUID

import structlog
from backend.app.api.deps import (
    get_current_user,
    get_db,
    get_file_service,
    get_rag_service,
)
from backend.app.core.config import settings
from backend.app.core.exceptions import ResourceNotFoundError
from backend.app.domain.file.schemas import (
    FileChunkResponse,
    FileResponse,
    RAGQueryRequest,
    RAGQueryResult,
)
from backend.app.domain.user.models import User
from backend.app.services.file_service import FileService
from backend.app.services.rag_service import RAGService
from fastapi import APIRouter, Depends, HTTPException, UploadFile, status
from fastapi import File as FastAPIFile
from sqlmodel.ext.asyncio.session import AsyncSession

router = APIRouter(prefix="/files", tags=["files"])
logger = structlog.get_logger(__name__)


@router.post("/upload", response_model=FileResponse, status_code=status.HTTP_201_CREATED)
async def upload_file(
    file: UploadFile = FastAPIFile(...),
    conversation_id: UUID | None = None,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
    file_svc: FileService = Depends(get_file_service),
):
    """Upload a file to cloud storage, then chunk and index it for RAG.

    With ASYNC_INDEXING=true the row is handed to the Celery worker
    (process_file_indexing_task) and returned with status='pending';
    otherwise it is ingested synchronously in the request.
    """
    filename = file.filename or "uploaded_document"
    file_bytes = await file.read()
    try:
        db_file = await file_svc.upload(
            file_bytes=file_bytes,
            filename=filename,
            content_type=file.content_type or "application/octet-stream",
            user_id=current_user.id,
            session=session,
            conversation_id=conversation_id,
        )

        # Async route: dispatch to the Celery worker (with sync fallback if the
        # broker is unreachable). Sync route: ingest now.
        if settings.ASYNC_INDEXING:
            try:
                from backend.app.worker.tasks import process_file_indexing_task

                process_file_indexing_task.delay(str(db_file.id))
                logger.info("file_indexing_dispatched_to_worker", file_id=str(db_file.id))
                return db_file
            except Exception as exc:
                logger.warning("async_indexing_dispatch_failed_falling_back_to_sync", file_id=str(db_file.id), error=str(exc))

        return await file_svc.ingest_bytes(
            db_file_id=db_file.id,
            file_bytes=file_bytes,
            original_filename=filename,
            user_id=current_user.id,
            session=session,
            conversation_id=conversation_id,
        )
    except ResourceNotFoundError as exc:
        raise HTTPException(status_code=404, detail=exc.message)


@router.get("", response_model=list[FileResponse])
async def list_files(
    conversation_id: UUID | None = None,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
    file_svc: FileService = Depends(get_file_service),
):
    """List all files belonging to the authenticated user."""
    return await file_svc.list_files(
        user_id=current_user.id,
        session=session,
        conversation_id=conversation_id,
    )


@router.get("/{file_id}/chunks", response_model=list[FileChunkResponse])
async def get_chunks(
    file_id: UUID,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
    file_svc: FileService = Depends(get_file_service),
):
    """Retrieve parsed text chunks for a specific file."""
    try:
        return await file_svc.get_file_chunks(
            file_id=file_id,
            user_id=current_user.id,
            session=session,
        )
    except ResourceNotFoundError as exc:
        raise HTTPException(status_code=404, detail=exc.message)


@router.delete("/{file_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_file(
    file_id: UUID,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
    file_svc: FileService = Depends(get_file_service),
):
    """Delete a file and remove its vector index points."""
    try:
        await file_svc.delete_file(
            file_id=file_id,
            user_id=current_user.id,
            session=session,
        )
    except ResourceNotFoundError as exc:
        raise HTTPException(status_code=404, detail=exc.message)


@router.post("/rag/query", response_model=RAGQueryResult)
async def query_rag(
    rag_in: RAGQueryRequest,
    current_user: User = Depends(get_current_user),
    rag_svc: RAGService = Depends(get_rag_service),
):
    """Execute a hybrid RAG query with cross-encoder reranking and formatted citations."""
    return await rag_svc.query(
        query=rag_in.query,
        user_id=current_user.id,
        file_ids=rag_in.file_ids,
        top_k=rag_in.top_k,
        score_threshold=rag_in.score_threshold,
    )
