"""
Files and RAG API Router.
Pure HTTP transport layer — delegates all file operations to FileService
and all RAG search operations to RAGService (SRP + DIP).
"""
from uuid import UUID

import structlog
from backend.app.api.deps import (
    get_current_org_id,
    get_current_user,
    get_db,
    get_file_service,
    get_rag_service,
    require_idempotency_key,
)
from backend.app.core.config import settings
from backend.app.core.exceptions import ResourceNotFoundError
from backend.app.domain.file.schemas import (
    FileChunkResponse,
    FileIndexStatusResponse,
    FileResponse,
    RAGQueryRequest,
    RAGQueryResult,
)
from backend.app.domain.user.models import User
from backend.app.infrastructure.resilience.rate_limit import rate_limit
from backend.app.services.file_service import FileService
from backend.app.services.rag_service import RAGService
from fastapi import APIRouter, Depends, HTTPException, Query, Response, UploadFile, status
from fastapi import File as FastAPIFile
from sqlmodel.ext.asyncio.session import AsyncSession

router = APIRouter(prefix="/files", tags=["files"])
logger = structlog.get_logger(__name__)


@router.post("/upload", response_model=FileResponse, status_code=status.HTTP_201_CREATED)
async def upload_file(
    file: UploadFile = FastAPIFile(...),
    conversation_id: UUID | None = None,
    current_user: User = Depends(get_current_user),
    current_org_id: UUID | None = Depends(get_current_org_id),
    session: AsyncSession = Depends(get_db),
    file_svc: FileService = Depends(get_file_service),
    response: Response = None,  # type: ignore[assignment]  # noqa: F821
    _idem_key: None = require_idempotency_key("file.upload"),
    _rl: None = rate_limit("file.upload", limit=30, window_seconds=60, org_scope=True),
):
    """Upload a file to cloud storage, then chunk and index it for RAG.

    With ASYNC_INDEXING=true the row is handed to the Celery worker
    (process_file_indexing_task) and answered 202 Accepted with a ``Location``
    header pointing at ``GET /files/{id}/index-status`` (async job pattern —
    roadmap §6.3); otherwise it is ingested synchronously and answered 201.
    """
    filename = file.filename or "uploaded_document"
    # Enforce an upload size ceiling BEFORE buffering the whole body into
    # memory: rejects oversized uploads early instead of loading them.
    max_bytes = settings.MAX_UPLOAD_SIZE_MB * 1024 * 1024
    if file.size is not None and file.size > max_bytes:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"File exceeds the {settings.MAX_UPLOAD_SIZE_MB}MB upload limit.",
        )
    chunks: list[bytes] = []
    total = 0
    while True:
        part = await file.read(1024 * 1024)
        if not part:
            break
        total += len(part)
        if total > max_bytes:
            raise HTTPException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail=f"File exceeds the {settings.MAX_UPLOAD_SIZE_MB}MB upload limit.",
            )
        chunks.append(part)
    file_bytes = b"".join(chunks)
    try:
        db_file = await file_svc.upload(
            file_bytes=file_bytes,
            filename=filename,
            content_type=file.content_type or "application/octet-stream",
            user_id=current_user.id,
            session=session,
            conversation_id=conversation_id,
        )

        # Async route: dispatch to the Celery worker through the EventPublisher
        # seam (with sync fallback if the dispatch fails or the broker is
        # unreachable). Sync route: ingest now.
        if settings.ASYNC_INDEXING:
            from backend.app.infrastructure.events import get_event_publisher
            from backend.app.infrastructure.events.base import DomainEvent

            dispatched = await get_event_publisher().publish(
                DomainEvent(
                    event_type="file.index_requested",
                    aggregate_id=str(db_file.id),
                    user_id=current_user.id,
                    org_id=str(current_org_id) if current_org_id else None,
                    payload={
                        "file_id": str(db_file.id),
                        "filename": filename,
                        "conversation_id": str(conversation_id) if conversation_id else None,
                    },
                )
            )
            if dispatched:
                logger.info("file_indexing_dispatched_to_worker", file_id=str(db_file.id))
                # 202 Accepted + Location: the job is queued, poll the index-status
                # endpoint (roadmap §6.3 async job pattern).
                response.status_code = status.HTTP_202_ACCEPTED
                response.headers["Location"] = f"{settings.API_V1_PREFIX}/files/{db_file.id}/index-status"
                return db_file
            logger.warning("async_indexing_dispatch_failed_falling_back_to_sync", file_id=str(db_file.id))

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
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0, le=1000000),
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
    file_svc: FileService = Depends(get_file_service),
):
    """List all files belonging to the authenticated user (paginated)."""
    return await file_svc.list_files(
        user_id=current_user.id,
        session=session,
        conversation_id=conversation_id,
        limit=limit,
        offset=offset,
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


@router.get("/{file_id}/index-status", response_model=FileIndexStatusResponse)
async def get_index_status(
    file_id: UUID,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
    file_svc: FileService = Depends(get_file_service),
):
    """Poll the async indexing job (§6.3). 404 if the file is not owned."""
    try:
        return await file_svc.get_file_index_status(
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
