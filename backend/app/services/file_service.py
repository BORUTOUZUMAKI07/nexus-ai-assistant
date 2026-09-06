"""
File Application Service.

Implements SRP: this service owns the entire file lifecycle use case
(upload → cloud storage → DB persist → ingest → chunk → embed → index).

API route controllers depend only on this service abstraction, never on
low-level infrastructure clients directly (DIP).
"""
import tempfile
from pathlib import Path
from uuid import UUID, uuid4

import structlog
from backend.app.core.exceptions import ResourceNotFoundError
from backend.app.domain.file.models import File, FileChunk
from backend.app.domain.file.repository import FileRepository
from backend.app.infrastructure.storage.base import IStorageService
from backend.app.services.rag.ingest import IngestionService
from sqlmodel.ext.asyncio.session import AsyncSession

logger = structlog.get_logger(__name__)


class FileService:
    """
    Application service orchestrating file upload, ingestion, retrieval, and deletion.
    Injected with infrastructure dependencies conforming to abstract interfaces (DIP).
    """

    def __init__(
        self,
        storage: IStorageService,
        ingestion: IngestionService,
    ) -> None:
        self._storage = storage
        self._ingestion = ingestion

    async def upload_and_ingest(
        self,
        *,
        file_bytes: bytes,
        filename: str,
        content_type: str,
        user_id: UUID,
        session: AsyncSession,
        conversation_id: UUID | None = None,
    ) -> File:
        """
        Full file lifecycle:
        1. Upload raw bytes to cloud storage (Supabase/S3/GCS via IStorageService).
        2. Persist a DB record with status='pending'.
        3. Extract text, chunk, embed, and index vectors in Qdrant.
        4. Update DB record status to 'indexed'.

        Returns the fully hydrated File domain object.
        """
        db_file = await self.upload(
            file_bytes=file_bytes,
            filename=filename,
            content_type=content_type,
            user_id=user_id,
            session=session,
            conversation_id=conversation_id,
        )
        return await self.ingest_bytes(
            db_file_id=db_file.id,
            file_bytes=file_bytes,
            original_filename=filename,
            user_id=user_id,
            session=session,
            conversation_id=conversation_id,
        )

    async def upload(
        self,
        *,
        file_bytes: bytes,
        filename: str,
        content_type: str,
        user_id: UUID,
        session: AsyncSession,
        conversation_id: UUID | None = None,
    ) -> File:
        """
        Steps 1-2 of the lifecycle only: persist raw bytes to cloud storage and
        create the DB record (status='pending'). The caller decides whether to
        ingest synchronously (ingest_bytes) or hand the row to the Celery worker.
        """
        repo = FileRepository(session)
        file_ext = Path(filename).suffix.lower()
        stored_name = f"{uuid4()}{file_ext}"
        storage_path = f"{user_id}/{stored_name}"

        logger.info("file_upload_started", filename=filename, user_id=str(user_id))

        cloud_path = await self._storage.upload(
            file_bytes=file_bytes,
            storage_path=storage_path,
            content_type=content_type,
        )

        return await repo.create_file(
            user_id=user_id,
            filename=stored_name,
            original_filename=filename,
            file_type=file_ext.replace(".", "") or "txt",
            mime_type=content_type,
            size_bytes=len(file_bytes),
            storage_path=cloud_path,
            conversation_id=conversation_id,
        )

    async def ingest_bytes(
        self,
        *,
        db_file_id: UUID,
        file_bytes: bytes,
        original_filename: str,
        user_id: UUID,
        session: AsyncSession,
        conversation_id: UUID | None = None,
    ) -> File:
        """Steps 3-4 of the lifecycle: chunk, embed, index, and refresh the DB row."""
        repo = FileRepository(session)
        db_file = await repo.get_by_id(db_file_id)
        if not db_file:
            raise ResourceNotFoundError(resource_type="File", identifier=str(db_file_id))

        file_ext = Path(original_filename).suffix.lower() or ".txt"
        with tempfile.NamedTemporaryFile(delete=False, suffix=file_ext) as tmp:
            tmp.write(file_bytes)
            tmp_path = Path(tmp.name)

        try:
            await self._ingestion.ingest_file_parent_child(
                file_id=db_file.id,
                file_path=tmp_path,
                filename=original_filename,
                user_id=user_id,
                session=session,
                conversation_id=conversation_id,
            )
        finally:
            tmp_path.unlink(missing_ok=True)

        refreshed = await repo.get_by_id(db_file.id)
        logger.info("file_ingested", file_id=str(db_file.id))
        return refreshed

    async def list_files(
        self,
        *,
        user_id: UUID,
        session: AsyncSession,
        conversation_id: UUID | None = None,
    ) -> list[File]:
        """Return all files belonging to a user, optionally scoped to a conversation."""
        repo = FileRepository(session)
        return await repo.get_by_user(user_id=user_id, conversation_id=conversation_id)

    async def get_file_chunks(
        self,
        *,
        file_id: UUID,
        user_id: UUID,
        session: AsyncSession,
    ) -> list[FileChunk]:
        """Return chunks for a file the user owns."""
        repo = FileRepository(session)
        file = await repo.get_by_id(file_id, user_id=user_id)
        if not file:
            raise ResourceNotFoundError(resource_type="File", identifier=str(file_id))
        return await repo.get_chunks_by_file(file_id)

    async def delete_file(
        self,
        *,
        file_id: UUID,
        user_id: UUID,
        session: AsyncSession,
    ) -> None:
        """
        Delete a file:
        1. Remove the cloud storage object (Supabase/S3/GCS).
        2. Remove relational DB records (file + chunks).
        3. Remove vector index points from Qdrant.
        """
        repo = FileRepository(session)
        db_file = await repo.get_by_id(file_id, user_id=user_id)
        if not db_file:
            raise ResourceNotFoundError(resource_type="File", identifier=str(file_id))

        if db_file.storage_path:
            try:
                await self._storage.delete(db_file.storage_path)
            except Exception as exc:
                logger.warning("storage_delete_failed_continuing", file_id=str(file_id), error=str(exc))

        await repo.delete_file(file_id, user_id=user_id)
        await self._ingestion.delete_file_index(file_id)
        logger.info("file_deleted", file_id=str(file_id), user_id=str(user_id))
