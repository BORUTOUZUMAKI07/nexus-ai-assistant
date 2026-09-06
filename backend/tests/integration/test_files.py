"""File upload, listing, chunks, deletion, and RAG query integration tests.

Storage and ingestion are faked at the dependency boundary; the DB persistence
paths (FileRepository) run against the real sqlite schema.
"""
import uuid
from pathlib import Path

import pytest
from backend.app.api import deps
from backend.app.core.config import settings as app_settings
from backend.app.core.exceptions import ResourceNotFoundError, UnsupportedFileTypeError
from backend.app.domain.file.repository import FileRepository
from backend.app.main import app

SAMPLE_CONTENT = b"# Report\n\nSome indexed report body for RAG retrieval."
FAKE_STORAGE_PATH = "fake/user-file.txt"


class FakeFileService:
    """Application-service double persisting through the real FileRepository."""

    @staticmethod
    async def upload(
        *,
        file_bytes: bytes,
        filename: str,
        content_type: str,
        user_id: uuid.UUID,
        session,
        conversation_id=None,
    ):
        if Path(filename).suffix.lower() in {".sh", ".exe", ".bat"}:
            raise UnsupportedFileTypeError()
        repo = FileRepository(session)
        return await repo.create_file(
            user_id=user_id,
            filename=filename,
            original_filename=filename,
            file_type=Path(filename).suffix.lower(),
            mime_type=content_type,
            size_bytes=len(file_bytes),
            storage_path=FAKE_STORAGE_PATH,
            conversation_id=conversation_id,
        )

    @staticmethod
    async def ingest_bytes(
        *,
        db_file_id: uuid.UUID,
        file_bytes: bytes,
        original_filename: str,
        user_id: uuid.UUID,
        session,
        conversation_id=None,
    ):
        repo = FileRepository(session)
        await repo.add_chunks(
            db_file_id,
            [
                {
                    "chunk_index": 0,
                    "content": "Report annotation. Some indexed report body.",
                    "token_count": 9,
                    "metadata": {"source": original_filename},
                }
            ],
        )
        return await repo.update_status(db_file_id, "indexed", chunk_count=1)

    @staticmethod
    async def list_files(*, user_id, session, conversation_id=None):
        return await FileRepository(session).get_by_user(
            user_id, conversation_id=conversation_id
        )

    @staticmethod
    async def get_file_chunks(*, file_id, user_id, session):
        repo = FileRepository(session)
        if not await repo.get_by_id(file_id, user_id=user_id):
            raise ResourceNotFoundError("File", str(file_id))
        return await repo.get_chunks_by_file(file_id)

    @staticmethod
    async def delete_file(*, file_id, user_id, session):
        repo = FileRepository(session)
        if not await repo.delete_file(file_id, user_id):
            raise ResourceNotFoundError("File", str(file_id))


class FakeRAGService:
    """Stub pure RAG query result for the offline integration path."""

    @staticmethod
    async def query(*, query, user_id, file_ids=None, top_k=5, score_threshold=0.4):
        return {
            "query": query,
            "citations": [
                {
                    "file_id": str(file_ids[0]) if file_ids else str(uuid.uuid4()),
                    "filename": "report.txt",
                    "chunk_index": 0,
                    "score": 0.92,
                    "content_snippet": "Some indexed report body.",
                    "metadata": {},
                }
            ],
            "total_retrieved": 1,
            "query_variants": [query],
        }


@pytest.fixture(autouse=True)
def _override_file_dependencies(monkeypatch):
    """Route the file service (and RAG service) to in-process fakes and turn
    async Celery indexing off so the synchronous ingest path runs."""
    monkeypatch.setattr(app_settings, "ASYNC_INDEXING", False)
    app.dependency_overrides[deps.get_file_service] = lambda: FakeFileService()
    app.dependency_overrides[deps.get_rag_service] = lambda: FakeRAGService()
    yield
    app.dependency_overrides.pop(deps.get_file_service, None)
    app.dependency_overrides.pop(deps.get_rag_service, None)


@pytest.mark.asyncio
async def test_upload_and_ingest_sync(client, user_auth_headers):
    resp = await client.post(
        "/api/v1/files/upload",
        files={
            "file": ("report.txt", SAMPLE_CONTENT, "text/plain"),
        },
        headers=user_auth_headers,
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["filename"] == "report.txt"
    assert body["status"] == "indexed"
    assert body["chunk_count"] == 1
    assert body["size_bytes"] == len(SAMPLE_CONTENT)
    assert body["storage_path"] == FAKE_STORAGE_PATH


@pytest.mark.asyncio
async def test_list_files_and_chunks(client, user_auth_headers):
    upload = await client.post(
        "/api/v1/files/upload",
        files={"file": ("notes.txt", b"notes body here", "text/plain")},
        headers=user_auth_headers,
    )
    file_id = upload.json()["id"]

    listing = await client.get("/api/v1/files", headers=user_auth_headers)
    assert listing.status_code == 200
    assert [f["id"] for f in listing.json()] == [file_id]

    chunks = await client.get(
        f"/api/v1/files/{file_id}/chunks", headers=user_auth_headers
    )
    assert chunks.status_code == 200
    assert chunks.json()[0]["chunk_index"] == 0
    assert chunks.json()[0]["metadata_json"]["source"] == "notes.txt"


@pytest.mark.asyncio
async def test_chunks_visibility_private_to_owner(client, user_auth_headers):
    upload = await client.post(
        "/api/v1/files/upload",
        files={"file": ("secret.txt", b"secret body", "text/plain")},
        headers=user_auth_headers,
    )
    file_id = upload.json()["id"]

    suffix = uuid.uuid4().hex[:8]
    await client.post(
        "/api/v1/auth/register",
        json={
            "email": f"snoop-{suffix}@example.com",
            "username": f"snoop-{suffix}",
            "password": "StrongPass123!",
        },
    )
    login = await client.post(
        "/api/v1/auth/login",
        data={"username": f"snoop-{suffix}@example.com", "password": "StrongPass123!"},
    )
    snoop_headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

    forbidden = await client.get(
        f"/api/v1/files/{file_id}/chunks", headers=snoop_headers
    )
    assert forbidden.status_code == 404


@pytest.mark.asyncio
async def test_delete_file(client, user_auth_headers):
    upload = await client.post(
        "/api/v1/files/upload",
        files={"file": ("del.txt", b"delete me", "text/plain")},
        headers=user_auth_headers,
    )
    file_id = upload.json()["id"]

    deleted = await client.delete(f"/api/v1/files/{file_id}", headers=user_auth_headers)
    assert deleted.status_code == 204

    listing = await client.get("/api/v1/files", headers=user_auth_headers)
    assert listing.json() == []


@pytest.mark.asyncio
async def test_rag_query_returns_citations(client, user_auth_headers):
    upload = await client.post(
        "/api/v1/files/upload",
        files={"file": ("report.txt", SAMPLE_CONTENT, "text/plain")},
        headers=user_auth_headers,
    )
    file_id = upload.json()["id"]

    resp = await client.post(
        "/api/v1/files/rag/query",
        json={"query": "what does the report say", "file_ids": [file_id]},
        headers=user_auth_headers,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["total_retrieved"] == 1
    assert body["citations"][0]["filename"] == "report.txt"


@pytest.mark.asyncio
async def test_files_require_auth(client):
    assert (await client.get("/api/v1/files")).status_code == 401


@pytest.mark.asyncio
async def test_upload_invalid_extension_rejected(client, user_auth_headers):
    resp = await client.post(
        "/api/v1/files/upload",
        files={"file": ("evil.sh", b"rm -rf /", "application/x-sh")},
        headers=user_auth_headers,
    )
    assert resp.status_code == 415
