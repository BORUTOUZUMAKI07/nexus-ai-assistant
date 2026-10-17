"""RAG ingestion pipeline regression: every chunk must become a vector point.

The ingest loop was briefly unreachable -- its body sat after an unconditional
``raise`` (the embedding-size guard) instead of under a ``for`` over the zipped
chunks/vectors. The failure was silent in the worst way: a successful ingest
still reported ``chunks == len(chunks)`` while writing **zero** Qdrant points and
**zero** chunk rows, so the file looked indexed and was unsearchable. Only a
static checker (ruff's F821 on the undefined ``c``/``dense_vec``) noticed.

This pins the loop behaviourally: one point and one persisted chunk row per
chunk, each carrying a Qdrant point id.
"""
from pathlib import Path
from uuid import uuid4

from backend.app.domain.file.models import File
from backend.app.services.rag.ingest import IngestionService

from tests.fakes import FakeSession


class _FakeRetriever:
    """Returns one dense vector per input text; a constant sparse vector."""

    async def generate_embeddings_batch(self, texts: list[str]) -> list[list[float]]:
        return [[0.1, 0.2] for _ in texts]

    def generate_sparse_vector(self, text: str) -> dict[str, list]:
        return {"indices": [1], "values": [1.0]}


class _FakeVectorStore:
    def __init__(self) -> None:
        self.upserted: list[dict] = []
        self.deleted: list[dict] = []

    async def upsert_points(self, points: list[dict]) -> None:
        self.upserted.extend(points)

    async def delete_by_filter(self, filter_conditions: dict) -> None:
        self.deleted.append(filter_conditions)


async def test_every_chunk_is_indexed_and_persisted(tmp_path: Path) -> None:
    file_id = uuid4()
    user_id = uuid4()
    path = tmp_path / "doc.md"
    path.write_text(
        "# Title\n\n" + "Nexus indexes this sentence into the vector store. " * 20,
        encoding="utf-8",
    )

    session = FakeSession()
    session.seed(
        File,
        [
            File(
                id=file_id,
                user_id=user_id,
                filename="doc.md",
                original_filename="doc.md",
                file_type="md",
                mime_type="text/markdown",
                size_bytes=path.stat().st_size,
                storage_path="docs/doc.md",
                status="processing",
            )
        ],
    )

    store = _FakeVectorStore()
    svc = IngestionService(vector_store=store, retriever=_FakeRetriever())

    result = await svc.ingest_file(file_id, path, "doc.md", user_id, session)

    assert result["status"] == "success"
    # The fixture must actually contain a chunk, or the assertions below pass
    # vacuously over empty collections (AGENTS.md 9.13).
    assert result["chunks"] >= 1

    # One distinct point per chunk, each carrying both vectors and its text.
    assert len(store.upserted) == result["chunks"]
    assert len({p["id"] for p in store.upserted}) == result["chunks"]
    assert all(
        p["vector"]["dense"] and p["vector"]["sparse"] and p["payload"]["content"]
        for p in store.upserted
    )

    # The relational side must agree with the vector side: every point id has a
    # matching persisted chunk row.
    chunk_rows = [r for r in session.added if getattr(r, "qdrant_point_id", None)]
    assert len(chunk_rows) == result["chunks"]
    assert {r.qdrant_point_id for r in chunk_rows} == {p["id"] for p in store.upserted}
