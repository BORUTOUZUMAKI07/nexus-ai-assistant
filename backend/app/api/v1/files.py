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
    get_read_db,
    require_idempotency_key,
)
from backend.app.core.config import settings
from backend.app.core.exceptions import ResourceNotFoundError
from backend.app.domain.file.schemas import (
    FileChunkResponse,
    FileDownloadResponse,
    FileIndexStatusResponse,
    FileResponse,
    RAGQueryRequest,
    RAGQueryResult,
)
from backend.app.domain.user.models import User
from backend.app.infrastructure.resilience.rate_limit import rate_limit
from backend.app.services.files.service import FileService
from backend.app.services.rag.service import RAGService
from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile, status
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
    _idem_key: None = require_idempotency_key("file.upload"),
    _rl: None = rate_limit("file.upload", limit=30, window_seconds=60, org_scope=True),
):
    """Upload a file to cloud storage, then chunk and index it for RAG inline.

    Answered 201 only when the document is indexed and searchable. There is no
    queue: a background consumer needs an always-on worker process, which the
    free deployment target does not provide, so the work stays on this request.
    ``GET /files/{id}/index-status`` remains for polling a row after a failure.
    """
    filename = file.filename or "uploaded_document"
    # Enforce an upload size ceiling BEFORE buffering the whole body into
    # memory: rejects oversized uploads early instead of loading them.
    max_bytes = settings.MAX_UPLOAD_SIZE_MB * 1024 * 1024

    # Asked before the body is read. Whether embeddings can run here is a
    # property of the deployment, not of the file: checking it now means an
    # upload to a host that cannot embed answers 503 in milliseconds instead
    # of buffering megabytes and then taking the process down with it.
    from backend.app.services.rag.retrieval import ensure_embedding_backend_available

    ensure_embedding_backend_available()

    def _too_large(size_bytes: int) -> HTTPException:
        """Say how big the file is and what to do, not just "too large".

        The client shows this detail verbatim, so "File exceeds the 4MB upload
        limit" alone leaves the user guessing whether to retry, split, or
        compress. Naming the actual size makes the fix obvious.
        """
        return HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail=(
                f"File is {size_bytes / (1024 * 1024):.1f}MB; the limit is "
                f"{settings.MAX_UPLOAD_SIZE_MB}MB. Split the document into smaller "
                "files, or raise MAX_UPLOAD_SIZE_MB on a deployment whose proxy "
                "does not cap the request body."
            ),
        )

    if file.size is not None and file.size > max_bytes:
        raise _too_large(file.size)
    # One growable buffer, not a list of chunks joined at the end: the join
    # held the whole file twice at peak, which matters on a small instance.
    file_buffer = bytearray()
    total = 0
    while True:
        part = await file.read(1024 * 1024)
        if not part:
            break
        total += len(part)
        if total > max_bytes:
            raise _too_large(total)
        file_buffer.extend(part)
    file_bytes = bytes(file_buffer)
    del file_buffer
    try:
        if conversation_id is not None:
            from backend.app.domain.conversation.repository import ConversationRepository
            convo = await ConversationRepository(session).get_by_id(conversation_id, user_id=current_user.id)
            if not convo:
                raise HTTPException(status_code=404, detail="Conversation not found")

        db_file = await file_svc.upload(
            file_bytes=file_bytes,
            filename=filename,
            content_type=file.content_type or "application/octet-stream",
            user_id=current_user.id,
            session=session,
            conversation_id=conversation_id,
        )

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
    session: AsyncSession = Depends(get_read_db),
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
    session: AsyncSession = Depends(get_read_db),
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
    """Poll the async 202 indexing job. 404 if the file is not owned."""
    try:
        return await file_svc.get_file_index_status(
            file_id=file_id,
            user_id=current_user.id,
            session=session,
        )
    except ResourceNotFoundError as exc:
        raise HTTPException(status_code=404, detail=exc.message)


@router.get("/{file_id}/download", response_model=FileDownloadResponse)
async def download_file(
    file_id: UUID,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
    file_svc: FileService = Depends(get_file_service),
):
    """Return a signed download URL for a file the user owns.

    The client downloads directly from object storage via the signed URL,
    so object bytes never transit the BFF.
    """
    try:
        download_url, filename, content_type, size_bytes = await file_svc.get_download_url(
            file_id=file_id,
            user_id=current_user.id,
            session=session,
        )
    except ResourceNotFoundError as exc:
        raise HTTPException(status_code=404, detail=exc.message)
    return FileDownloadResponse(
        download_url=download_url,
        filename=filename,
        content_type=content_type,
        size_bytes=size_bytes,
    )


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
