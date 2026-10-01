"""
Repository for Artifact domain operations.
"""
from datetime import UTC, datetime
from uuid import UUID

from backend.app.domain.artifact.models import Artifact, ArtifactVersion
from backend.app.domain.base_repository import BaseRepository
from sqlmodel import delete, select
from sqlmodel.ext.asyncio.session import AsyncSession


class ArtifactRepository(BaseRepository[Artifact]):
    def __init__(self, session: AsyncSession):
        super().__init__(session, Artifact)

    # ── reads ────────────────────────────────────────────────────────────────

    async def get_by_id(self, artifact_id: UUID, user_id: UUID | None = None) -> Artifact | None:
        """Fetch an artifact, optionally owner-scoped (IDOR guard)."""
        statement = select(Artifact).where(Artifact.id == artifact_id)
        if user_id:
            statement = statement.where(Artifact.user_id == user_id)
        result = await self.session.exec(statement)
        return result.first()

    async def list_for_user(
        self, user_id: UUID, conversation_id: UUID | None = None, limit: int = 50
    ) -> list[Artifact]:
        statement = select(Artifact).where(Artifact.user_id == user_id)
        if conversation_id:
            statement = statement.where(Artifact.conversation_id == conversation_id)
        statement = statement.order_by(Artifact.updated_at.desc()).limit(limit)
        result = await self.session.exec(statement)
        return list(result.all())

    async def find_by_conversation_title(
        self, user_id: UUID, conversation_id: UUID, title: str
    ) -> Artifact | None:
        """Find the artifact a regeneration of ``title`` would revise.

        Scoped to (user, conversation, title) on purpose. The agent node runs
        *inside* the graph, which finishes before the assistant message row
        exists, so ``message_id`` is not available to key on -- and keying on
        the title alone would let a document in one conversation collide with
        an unrelated document of the same name in another.

        The title is derived deterministically from the content
        (``artifact_intent.derive_title``), so regenerating the same document
        lands on the same title and becomes a new *version* instead of a
        duplicate. That is what keeps ``artifact_versions`` reachable: without
        this lookup every regeneration would silently orphan the history the
        table exists to hold.
        """
        statement = (
            select(Artifact)
            .where(Artifact.user_id == user_id)
            .where(Artifact.conversation_id == conversation_id)
            .where(Artifact.title == title)
            .order_by(Artifact.updated_at.desc())
        )
        result = await self.session.exec(statement)
        return result.first()

    async def list_versions(self, artifact_id: UUID) -> list[ArtifactVersion]:
        statement = (
            select(ArtifactVersion)
            .where(ArtifactVersion.artifact_id == artifact_id)
            .order_by(ArtifactVersion.version.desc())
        )
        result = await self.session.exec(statement)
        return list(result.all())

    # ── writes ───────────────────────────────────────────────────────────────

    async def create_artifact(self, payload: dict) -> Artifact:
        artifact = Artifact(**payload, version=1)
        self.session.add(artifact)
        await self.session.commit()
        await self.session.refresh(artifact)
        return artifact

    async def snapshot_version(self, artifact: Artifact) -> None:
        """Persist the current artifact state as an immutable version row."""
        previous = ArtifactVersion(
            artifact_id=artifact.id,
            version=artifact.version,
            title=artifact.title,
            language=artifact.language,
            mime_type=artifact.mime_type,
            content=artifact.content,
        )
        self.session.add(previous)
        await self.session.flush()

    async def bump_version(self, artifact: Artifact, patch: dict) -> Artifact:
        """Snapshot the current content, then apply a new version onto the row."""
        await self.snapshot_version(artifact)
        for key, value in patch.items():
            if value is not None and hasattr(artifact, key):
                setattr(artifact, key, value)
        artifact.version += 1
        artifact.updated_at = datetime.now(UTC).replace(tzinfo=None)
        self.session.add(artifact)
        await self.session.commit()
        await self.session.refresh(artifact)
        return artifact

    async def delete_artifact(self, artifact: Artifact) -> None:
        """Hard-delete an artifact and all its version snapshots."""
        await self.session.exec(
            delete(ArtifactVersion).where(ArtifactVersion.artifact_id == artifact.id)
        )
        await self.session.delete(artifact)
        await self.session.commit()

    # ── cleanup cascades ─────────────────────────────────────────────────────

    async def delete_for_conversation(self, conversation_id: UUID) -> None:
        """Remove artifact versions → artifacts for a conversation."""
        artifact_sub = select(Artifact.id).where(
            Artifact.conversation_id == conversation_id
        )
        await self.session.exec(
            delete(ArtifactVersion).where(ArtifactVersion.artifact_id.in_(artifact_sub))
        )
        await self.session.exec(
            delete(Artifact).where(Artifact.conversation_id == conversation_id)
        )

    async def delete_for_user(self, user_id: UUID) -> None:
        """Remove artifact versions → artifacts a user owns (account erasure)."""
        artifact_sub = select(Artifact.id).where(Artifact.user_id == user_id)
        await self.session.exec(
            delete(ArtifactVersion).where(ArtifactVersion.artifact_id.in_(artifact_sub))
        )
        await self.session.exec(delete(Artifact).where(Artifact.user_id == user_id))
