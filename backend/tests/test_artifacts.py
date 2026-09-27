"""
Unit tests for the Artifacts backend builder (persisted + versioned).

Covered:
  * ArtifactService semantics against the in-memory FakeSession
  * versioning: writing v2 must preserve the previous content in
    ``artifact_versions`` (never destroyed)
  * owner-scoped CRUD routes via ASGI overrides (IDOR 404s included)
  * the 200_000-char cap enforcing a 422
"""
import uuid

import pytest
from backend.app.api import deps
from backend.app.core.exceptions import ResourceNotFoundError
from backend.app.domain.artifact.models import Artifact, ArtifactVersion
from backend.app.domain.artifact.schemas import ArtifactCreate, ArtifactVersionCreate
from backend.app.main import app
from backend.app.services.artifact_service import ArtifactService
from backend.tests.fakes import FakeSession
from httpx import ASGITransport, AsyncClient


def _make_user() -> object:
    return type("User", (), {"id": uuid.uuid4(), "role": "user", "email": "u@x.io", "username": "u"})()


# ─────────────────────────────────────────────────────────────────────
# Service
# ─────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_create_artifact():
    fake = FakeSession()
    user_id = uuid.uuid4()
    artifact = await ArtifactService(fake).create(
        user_id, ArtifactCreate(title="Spec", content="# Title", language="markdown")
    )
    assert artifact.user_id == user_id
    assert artifact.title == "Spec"
    assert artifact.version == 1
    assert artifact.conversation_id is None


@pytest.mark.asyncio
async def test_add_version_snapshots_previous_content():
    fake = FakeSession()
    user_id = uuid.uuid4()
    service = ArtifactService(fake)
    artifact = await service.create(user_id, ArtifactCreate(title="v1 doc", content="ORIGINAL"))
    original_id = artifact.id

    bumped = await service.add_version(
        original_id, user_id, ArtifactVersionCreate(content="UPDATED v2")
    )
    assert bumped.version == 2
    assert bumped.content == "UPDATED v2"

    versions = await service.list_versions(original_id)
    assert len(versions) == 1
    assert versions[0].version == 1
    assert versions[0].content == "ORIGINAL"


@pytest.mark.asyncio
async def test_get_respects_owner_scope():
    fake = FakeSession()
    owner = uuid.uuid4()
    stranger = uuid.uuid4()
    artifact = Artifact(
        user_id=owner, title="P", language="markdown", mime_type="text/plain", content="x", version=1
    )
    fake.seed(Artifact, [artifact])

    assert (await ArtifactService(fake).get(artifact.id, owner)).id == artifact.id
    with pytest.raises(ResourceNotFoundError):
        await ArtifactService(fake).get(artifact.id, stranger)


@pytest.mark.asyncio
async def test_delete_removes_artifact_and_versions():
    fake = FakeSession()
    user_id = uuid.uuid4()
    artifact = Artifact(
        user_id=user_id, title="P", language="markdown", mime_type="text/plain", content="x", version=2
    )
    fake.seed(Artifact, [artifact])
    fake.seed(ArtifactVersion, [ArtifactVersion(artifact_id=artifact.id, version=1, title="P", content="old")])

    await ArtifactService(fake).delete(artifact.id, user_id)
    with pytest.raises(ResourceNotFoundError):
        await ArtifactService(fake).get(artifact.id, user_id)


@pytest.mark.asyncio
async def test_delete_foreign_artifact_404():
    fake = FakeSession()
    owner = uuid.uuid4()
    artifact = Artifact(
        user_id=owner, title="P", language="markdown", mime_type="text/plain", content="x"
    )
    fake.seed(Artifact, [artifact])
    with pytest.raises(ResourceNotFoundError):
        await ArtifactService(fake).delete(artifact.id, uuid.uuid4())


# ─────────────────────────────────────────────────────────────────────
# API routes (ASGI overrides)
# ─────────────────────────────────────────────────────────────────────

def _apply_overrides(fake: FakeSession, user):
    from backend.app.services.conversation_service import ConversationService

    async def _override_db():
        yield fake

    async def _override_user():
        return user

    async def _artifact_service():
        return ArtifactService(fake)

    async def _conv_service():
        return ConversationService(fake)

    app.dependency_overrides[deps.get_current_user] = _override_user
    app.dependency_overrides[deps.get_db] = _override_db
    app.dependency_overrides[deps.get_artifact_service] = _artifact_service
    app.dependency_overrides[deps.get_conversation_service] = _conv_service


@pytest.mark.asyncio
async def test_create_and_list_artifacts_api():
    user = _make_user()
    fake = FakeSession()
    _apply_overrides(fake, user)

    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            created = await client.post(
                "/api/v1/artifacts",
                json={"title": "Deck", "language": "markdown", "content": "# Slides"},
            )
            assert created.status_code == 201
            data = created.json()
            assert data["version"] == 1
            assert data["user_id"] == str(user.id)

            listed = await client.get("/api/v1/artifacts")
            assert listed.status_code == 200
            assert len(listed.json()) == 1
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_version_cycle_via_api():
    user = _make_user()
    fake = FakeSession()
    _apply_overrides(fake, user)

    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            created = await client.post(
                "/api/v1/artifacts", json={"title": "Doc", "content": "REV 1"}
            )
            artifact_id = created.json()["id"]

            bumped = await client.post(
                f"/api/v1/artifacts/{artifact_id}/versions", json={"content": "REV 2"}
            )
            assert bumped.status_code == 200
            assert bumped.json()["version"] == 2
            assert bumped.json()["content"] == "REV 2"

            detail = await client.get(f"/api/v1/artifacts/{artifact_id}")
            assert detail.status_code == 200
            versions = detail.json()["versions"]
            assert len(versions) == 1
            assert versions[0]["content"] == "REV 1"
            assert versions[0]["version"] == 1
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_artifact_idor_404_and_delete():
    user = _make_user()
    stranger = _make_user()
    fake = FakeSession()
    artifact = Artifact(
        user_id=stranger.id, title="P", language="markdown", mime_type="text/plain", content="x", version=1
    )
    fake.seed(Artifact, [artifact])
    _apply_overrides(fake, user)

    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            fetched = await client.get(f"/api/v1/artifacts/{artifact.id}")
            assert fetched.status_code == 404

            deleted = await client.delete(f"/api/v1/artifacts/{artifact.id}")
            assert deleted.status_code == 404
    finally:
        app.dependency_overrides.clear()

    # owner delete works
    fake2 = FakeSession()
    artifact2 = Artifact(
        user_id=stranger.id, title="P", language="markdown", mime_type="text/plain", content="x", version=1
    )
    fake2.seed(Artifact, [artifact2])
    _apply_overrides(fake2, stranger)
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            deleted = await client.delete(f"/api/v1/artifacts/{artifact2.id}")
            assert deleted.status_code == 204
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_artifact_content_cap_enforced_422():
    user = _make_user()
    fake = FakeSession()
    _apply_overrides(fake, user)

    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post(
                "/api/v1/artifacts", json={"title": "Too big", "content": "x" * 200_001}
            )
            assert resp.status_code == 422
    finally:
        app.dependency_overrides.clear()
