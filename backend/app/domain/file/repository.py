"""
Repository for File and Chunk domain operations.
"""
from datetime import datetime
from uuid import UUID

from backend.app.domain.base_repository import BaseRepository
from backend.app.domain.file.models import File, FileChunk
from sqlmodel import delete, select
from sqlmodel.ext.asyncio.session import AsyncSession


class FileRepository(BaseRepository[File]):
    def __init__(self, session: AsyncSession):
        super().__init__(session, File)

    async def get_by_id(self, file_id: UUID, user_id: UUID | None = None) -> File | None:
        statement = select(File).where(File.id == file_id)
        if user_id:
            statement = statement.where(File.user_id == user_id)
        result = await self.session.exec(statement)
        return result.first()

    async def get_by_user(self, user_id: UUID, conversation_id: UUID | None = None, limit: int = 50) -> list[File]:
        statement = select(File).where(File.user_id == user_id)
        if conversation_id:
            statement = statement.where(File.conversation_id == conversation_id)
        statement = statement.order_by(File.created_at.desc()).limit(limit)
        result = await self.session.exec(statement)
        return list(result.all())

    async def create_file(
        self,
        user_id: UUID,
        filename: str,
        original_filename: str,
        file_type: str,
        mime_type: str,
        size_bytes: int,
        storage_path: str,
        conversation_id: UUID | None = None,
    ) -> File:
        db_file = File(
            user_id=user_id,
            conversation_id=conversation_id,
            filename=filename,
            original_filename=original_filename,
            file_type=file_type,
            mime_type=mime_type,
            size_bytes=size_bytes,
            storage_path=storage_path,
            status="pending",
        )
        self.session.add(db_file)
        await self.session.commit()
        await self.session.refresh(db_file)
        return db_file

    async def update_status(self, file_id: UUID, status: str, chunk_count: int = 0, error_message: str | None = None) -> File | None:
        db_file = await self.get_by_id(file_id)
        if db_file:
            db_file.status = status
            db_file.chunk_count = chunk_count
            db_file.error_message = error_message
            db_file.updated_at = datetime.utcnow()
            self.session.add(db_file)
            await self.session.commit()
            await self.session.refresh(db_file)
        return db_file

    async def add_chunks(self, file_id: UUID, chunks_data: list[dict]) -> list[FileChunk]:
        """
        Persists chunk rows (parent and/or child) and flushes each so row IDs
        are assigned — child rows can then reference their parent_chunk_id.
        """
        db_chunks: list[FileChunk] = []
        for c in chunks_data:
            chunk = FileChunk(
                file_id=file_id,
                chunk_index=c["chunk_index"],
                content=c["content"],
                token_count=c.get("token_count", 0),
                metadata_json=c.get("metadata", {}),
                qdrant_point_id=c.get("qdrant_point_id"),
                parent_chunk_id=c.get("parent_chunk_id"),
                is_parent=c.get("is_parent", False),
                contextual_prefix=c.get("contextual_prefix"),
            )
            self.session.add(chunk)
            await self.session.flush()
            db_chunks.append(chunk)
        await self.session.commit()
        for chunk in db_chunks:
            await self.session.refresh(chunk)
        return db_chunks

    async def get_chunks_by_file(self, file_id: UUID) -> list[FileChunk]:
        statement = select(FileChunk).where(FileChunk.file_id == file_id).order_by(FileChunk.chunk_index.asc())
        result = await self.session.exec(statement)
        return list(result.all())

    async def delete_file(self, file_id: UUID, user_id: UUID) -> bool:
        db_file = await self.get_by_id(file_id, user_id=user_id)
        if not db_file:
            return False
        # Delete associated chunks first. A single bulk DELETE removes parent
        # and child chunks in one statement (satisfies the self-referential
        # parent_chunk_id FK) before the file row is removed.
        await self.session.exec(
            delete(FileChunk).where(FileChunk.file_id == file_id)
        )
        await self.session.delete(db_file)
        await self.session.commit()
        return True
