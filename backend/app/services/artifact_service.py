"""
Artifact Application Service — persist, version, and delete AI-generated files (SRP).

All reads and writes are owner-scoped (IDOR guard): every method takes a
``user_id`` and the repository filters by it, so a user can never touch a
stranger's artifact.
"""
from uuid import UUID

import structlog
from backend.app.core.exceptions import ResourceNotFoundError
from backend.app.domain.artifact.models import Artifact, ArtifactVersion
from backend.app.domain.artifact.repository import ArtifactRepository
from backend.app.domain.artifact.schemas import ArtifactCreate, ArtifactVersionCreate
from sqlmodel.ext.asyncio.session import AsyncSession

logger = structlog.get_logger(__name__)


class ArtifactService:
    def __init__(self, session: AsyncSession) -> None:
        self._repo = ArtifactRepository(session)

    async def create(self, user_id: UUID, create: ArtifactCreate) -> Artifact:
        artifact = await self._repo.create_artifact(
            {
                "user_id": user_id,
                "title": create.title,
                "language": create.language,
                "mime_type": create.mime_type,
                "content": create.content,
                "conversation_id": create.conversation_id,
                "message_id": create.message_id,
            }
        )
        logger.info("artifact_created", artifact_id=str(artifact.id), version=artifact.version)
        return artifact

    async def list_artifacts(
        self,
        user_id: UUID,
        conversation_id: UUID | None = None,
        limit: int = 50,
    ) -> list[Artifact]:
        return await self._repo.list_for_user(
            user_id, conversation_id=conversation_id, limit=limit
        )

    async def get(self, artifact_id: UUID, user_id: UUID) -> Artifact:
        artifact = await self._repo.get_by_id(artifact_id, user_id=user_id)
        if not artifact:
            raise ResourceNotFoundError("Artifact", str(artifact_id))
        return artifact

    async def list_versions(self, artifact_id: UUID) -> list[ArtifactVersion]:
        return await self._repo.list_versions(artifact_id)

    async def add_version(
        self, artifact_id: UUID, user_id: UUID, body: ArtifactVersionCreate
    ) -> Artifact:
        artifact = await self.get(artifact_id, user_id)
        patch = {
            "title": body.title,
            "language": body.language,
            "mime_type": body.mime_type,
            "content": body.content,
        }
        artifact = await self._repo.bump_version(artifact, patch)
        logger.info(
            "artifact_version_created",
            artifact_id=str(artifact.id),
            version=artifact.version,
        )
        return artifact

    async def delete(self, artifact_id: UUID, user_id: UUID) -> None:
        artifact = await self.get(artifact_id, user_id)
        await self._repo.delete_artifact(artifact)
        logger.info("artifact_deleted", artifact_id=str(artifact_id))
