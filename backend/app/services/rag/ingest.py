"""
RAG Ingestion Pipeline.
Handles text extraction across multiple formats (text, markdown, PDF, code, JSON),
semantic chunking, dual-vector generation (dense + sparse BM25),
Qdrant vector indexing, and relational chunk persistence.
"""
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import structlog
from backend.app.domain.file.repository import FileRepository
from backend.app.infrastructure.vector.base import IVectorStore
from backend.app.infrastructure.vector.qdrant_client import (
    vector_db as _default_vector_db,
)
from backend.app.services.rag.chunking import chunking_service
from backend.app.services.rag.retrieval import (
    retrieval_service as _default_retrieval_service,
)
from sqlmodel.ext.asyncio.session import AsyncSession

logger = structlog.get_logger(__name__)


class IngestionService:
    """
    Orchestrates the entire document ingestion and indexing pipeline.
    Accepts IVectorStore and retriever via constructor injection (DIP).
    """

    def __init__(
        self,
        vector_store: IVectorStore = _default_vector_db,
        retriever: Any = None,
    ) -> None:
        self._vector_store = vector_store
        self._retriever = retriever or _default_retrieval_service

    def extract_text(self, file_path: Path, mime_type: str | None = None) -> str:
        """
        Extracts raw text from various file formats.
        Supports plain text, markdown, PDF, JSON, and source code.
        """
        suffix = file_path.suffix.lower()

        if suffix in [".txt", ".md", ".py", ".js", ".ts", ".json", ".csv", ".yaml", ".yml", ".html", ".xml"]:
            try:
                return file_path.read_text(encoding="utf-8", errors="ignore")
            except Exception as e:
                logger.warning("direct_text_read_failed", path=str(file_path), error=str(e))
                return ""

        if suffix == ".pdf":
            # Attempt extraction via pypdf or pdfplumber if available
            try:
                import pypdf
                reader = pypdf.PdfReader(str(file_path))
                text_parts = []
                for idx, page in enumerate(reader.pages):
                    extracted = page.extract_text()
                    if extracted:
                        text_parts.append(f"--- Page {idx + 1} ---\n{extracted}")
                return "\n\n".join(text_parts)
            except ImportError:
                logger.warning("pypdf_not_installed_trying_raw_decode", path=str(file_path))
            except Exception as exc:
                logger.warning("pdf_extraction_failed", path=str(file_path), error=str(exc))

        # Fallback binary decode
        try:
            raw = file_path.read_bytes()
            return raw.decode("utf-8", errors="ignore")
        except Exception:
            return ""

    async def ingest_file(
        self,
        file_id: UUID,
        file_path: Path,
        filename: str,
        user_id: UUID,
        session: AsyncSession,
        conversation_id: UUID | None = None,
    ) -> dict[str, Any]:
        """
        Full ingestion pipeline:
        1. Extract text from disk
        2. Chunk document with header awareness
        3. Generate dense and sparse vectors
        4. Upsert into Qdrant collection
        5. Persist chunk records and update file status in database
        """
        repo = FileRepository(session)
        logger.info("ingestion_started", file_id=str(file_id), filename=filename, user_id=str(user_id))

        try:
            text_content = self.extract_text(file_path)
            if not text_content.strip():
                await repo.update_status(file_id, status="failed", error_message="Empty file or no readable text extracted")
                return {"status": "failed", "reason": "empty_content", "chunks": 0}

            metadata = {
                "file_id": str(file_id),
                "filename": filename,
                "user_id": str(user_id),
                "conversation_id": str(conversation_id) if conversation_id else None,
            }

            chunks = chunking_service.chunk_document(text_content, source_metadata=metadata)
            if not chunks:
                await repo.update_status(file_id, status="failed", error_message="Chunking produced zero chunks")
                return {"status": "failed", "reason": "no_chunks", "chunks": 0}

            points = []
            chunks_data = []

            for c in chunks:
                dense_vec = await self._retriever.generate_embedding(c.content)
                sparse_dict = self._retriever.generate_sparse_vector(c.content)
                point_id = str(uuid4())

                points.append({
                    "id": point_id,
                    "vector": {
                        "dense": dense_vec,
                        "sparse": sparse_dict,
                    },
                    "payload": {
                        "content": c.content,
                        "chunk_index": c.chunk_index,
                        "file_id": str(file_id),
                        "filename": filename,
                        "user_id": str(user_id),
                        **c.metadata,
                    },
                })

                chunks_data.append({
                    "chunk_index": c.chunk_index,
                    "content": c.content,
                    "token_count": c.token_count,
                    "metadata": c.metadata,
                    "qdrant_point_id": point_id,
                })

            # 1. Save chunks to relational database
            await repo.add_chunks(file_id, chunks_data)

            # 2. Upsert vector points into Qdrant via injected IVectorStore
            await self._vector_store.upsert_points(points=points)

            # 3. Mark file as indexed
            await repo.update_status(file_id, status="indexed", chunk_count=len(chunks))

            logger.info("ingestion_completed", file_id=str(file_id), total_chunks=len(chunks))
            return {"status": "success", "file_id": str(file_id), "chunks": len(chunks)}

        except Exception as exc:
            logger.exception("ingestion_pipeline_error", file_id=str(file_id), error=str(exc))
            await repo.update_status(file_id, status="failed", error_message=str(exc))
            return {"status": "failed", "error": str(exc), "chunks": 0}

    async def ingest_file_parent_child(
        self,
        file_id: UUID,
        file_path: Path,
        filename: str,
        user_id: UUID,
        session: AsyncSession,
        conversation_id: UUID | None = None,
    ) -> dict[str, Any]:
        """
        Parent-Child ingestion pipeline:
        1. Extract text from disk
        2. Split into 512-token parents, then 128-token children (32t overlap)
        3. Attach contextual prefix (title + heading) to every child
        4. Persist parent rows first, then children referencing parent row ids
        5. Embed children with their contextual prefix; store parent text alongside
           each child vector payload so a hit resolves to full-precision context
        6. Update file status in database
        """
        repo = FileRepository(session)
        logger.info("parent_child_ingestion_started", file_id=str(file_id), filename=filename, user_id=str(user_id))

        try:
            text_content = self.extract_text(file_path)
            if not text_content.strip():
                await repo.update_status(file_id, status="failed", error_message="Empty file or no readable text extracted")
                return {"status": "failed", "reason": "empty_content", "chunks": 0}

            metadata = {
                "file_id": str(file_id),
                "filename": filename,
                "user_id": str(user_id),
                "conversation_id": str(conversation_id) if conversation_id else None,
            }

            groups = chunking_service.chunk_with_children(text_content, source_metadata=metadata)
            if not groups:
                await repo.update_status(file_id, status="failed", error_message="Chunking produced zero parent chunks")
                return {"status": "failed", "reason": "no_chunks", "chunks": 0}

            # ── 1. Persist parent rows (their DB ids anchor children) ──────────────
            parents_data = [
                {
                    "chunk_index": g["parent"].chunk_index,
                    "content": g["parent"].content,
                    "token_count": g["parent"].token_count,
                    "metadata": g["parent"].metadata,
                    "is_parent": True,
                }
                for g in groups
            ]
            parent_rows = await repo.add_chunks(file_id, parents_data)
            parent_row_by_index = {row.chunk_index: row for row in parent_rows}

            # ── 2. Persist child rows + build Qdrant child points ─────────────────
            chunks_data: list[dict[str, Any]] = []
            points: list[dict[str, Any]] = []
            child_offset = len(parents_data)
            child_count = 0

            for group in groups:
                parent = group["parent"]
                parent_row = parent_row_by_index.get(parent.chunk_index)
                for child in group["children"]:
                    embedded_text = f"{child.contextual_prefix}\n{child.content}" if child.contextual_prefix else child.content
                    dense_vec = await self._retriever.generate_embedding(embedded_text)
                    sparse_dict = self._retriever.generate_sparse_vector(embedded_text)
                    point_id = str(uuid4())

                    points.append({
                        "id": point_id,
                        "vector": {
                            "dense": dense_vec,
                            "sparse": sparse_dict,
                        },
                        "payload": {
                            "content": child.content,
                            "contextual_prefix": child.contextual_prefix,
                            "embedded_text": embedded_text,
                            "chunk_index": child.chunk_index + child_offset,
                            "file_id": str(file_id),
                            "filename": filename,
                            "user_id": str(user_id),
                            "is_parent": False,
                            "parent_chunk_id": str(parent_row.id) if parent_row else None,
                            "parent_chunk_content": parent.content,
                            **child.metadata,
                        },
                    })

                    chunks_data.append({
                        "chunk_index": child.chunk_index + child_offset,
                        "content": child.content,
                        "token_count": child.token_count,
                        "metadata": child.metadata,
                        "qdrant_point_id": point_id,
                        "parent_chunk_id": parent_row.id if parent_row else None,
                        "is_parent": False,
                        "contextual_prefix": child.contextual_prefix,
                    })
                    child_count += 1

            await repo.add_chunks(file_id, chunks_data)

            # ── 3. Upsert child vector points into Qdrant ─────────────────────────
            if points:
                await self._vector_store.upsert_points(points=points)

            # ── 4. Mark file as indexed ────────────────────────────────────────────
            total_chunks = child_count
            await repo.update_status(file_id, status="indexed", chunk_count=total_chunks)

            logger.info(
                "parent_child_ingestion_completed",
                file_id=str(file_id),
                parent_count=len(parent_rows),
                child_count=child_count,
            )
            return {"status": "success", "file_id": str(file_id), "chunks": total_chunks}

        except Exception as exc:
            logger.exception("parent_child_ingestion_error", file_id=str(file_id), error=str(exc))
            await repo.update_status(file_id, status="failed", error_message=str(exc))
            return {"status": "failed", "error": str(exc), "chunks": 0}

    async def delete_file_index(self, file_id: UUID) -> bool:
        """
        Deletes all vector points associated with a file from Qdrant.
        """
        try:
            await self._vector_store.delete_by_filter(filter_conditions={"file_id": str(file_id)})
            logger.info("deleted_qdrant_vectors_for_file", file_id=str(file_id))
            return True
        except Exception as exc:
            logger.error("failed_to_delete_qdrant_vectors", file_id=str(file_id), error=str(exc))
            return False


# Singleton instance for backwards-compatibility (default IVectorStore = QdrantService)
ingestion_service = IngestionService()
