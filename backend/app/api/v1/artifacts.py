"""
Artifacts API Router — persisted, versioned AI-generated files.

Endpoint surface (all owner-scoped):
  POST   /artifacts                          create an artifact
  GET    /artifacts?conversation_id=&limit=  list own artifacts
  GET    /artifacts/{artifact_id}            detail incl. version history
  POST   /artifacts/{artifact_id}/versions   write a new version (keeps prior)
  DELETE /artifacts/{artifact_id}            delete artifact + all versions
"""
from uuid import UUID

from backend.app.api.deps import (
    get_artifact_service,
    get_conversation_service,
    get_current_user,
)
from backend.app.core.exceptions import ResourceNotFoundError
from backend.app.domain.artifact.schemas import (
    ArtifactCreate,
    ArtifactDetailResponse,
    ArtifactResponse,
    ArtifactVersionCreate,
    ArtifactVersionSnapshot,
)
from backend.app.domain.user.models import User
from backend.app.services.artifact_service import ArtifactService
from backend.app.services.conversation_service import ConversationService
from fastapi import APIRouter, Depends, HTTPException, Query, status

router = APIRouter(prefix="/artifacts", tags=["artifacts"])


@router.post("", response_model=ArtifactResponse, status_code=status.HTTP_201_CREATED)
async def create_artifact(
    body: ArtifactCreate,
    current_user: User = Depends(get_current_user),
    artifact_svc: ArtifactService = Depends(get_artifact_service),
    conv_svc: ConversationService = Depends(get_conversation_service),
) -> ArtifactResponse:
    """Persist a new artifact owned by the current user.

    ``conversation_id``/``message_id`` are optional provenance pointers; when
    given, the conversation must belong to the caller (IDOR + FK safety).
    """
    if body.conversation_id:
        try:
            await conv_svc.get_conversation(body.conversation_id, user_id=current_user.id)
        except ResourceNotFoundError:
            raise HTTPException(status_code=404, detail="Conversation not found")
    artifact = await artifact_svc.create(current_user.id, body)
    return ArtifactResponse.model_validate(artifact)


@router.get("", response_model=list[ArtifactResponse])
async def list_artifacts(
    conversation_id: UUID | None = None,
    limit: int = Query(default=50, le=200),
    current_user: User = Depends(get_current_user),
    artifact_svc: ArtifactService = Depends(get_artifact_service),
) -> list[ArtifactResponse]:
    """List the caller's artifacts, optionally filtered to one conversation."""
    artifacts = await artifact_svc.list_artifacts(current_user.id, conversation_id=conversation_id, limit=limit)
    return [ArtifactResponse.model_validate(a) for a in artifacts]


@router.get("/{artifact_id}", response_model=ArtifactDetailResponse)
async def get_artifact(
    artifact_id: UUID,
    current_user: User = Depends(get_current_user),
    artifact_svc: ArtifactService = Depends(get_artifact_service),
) -> ArtifactDetailResponse:
    """Fetch one artifact including its version history (newest first)."""
    artifact = await artifact_svc.get(artifact_id, current_user.id)
    versions = await artifact_svc.list_versions(artifact_id)
    detail = ArtifactDetailResponse.model_validate(artifact)
    detail.versions = [ArtifactVersionSnapshot.model_validate(v) for v in versions]
    return detail


@router.post("/{artifact_id}/versions", response_model=ArtifactResponse)
async def add_artifact_version(
    artifact_id: UUID,
    body: ArtifactVersionCreate,
    current_user: User = Depends(get_current_user),
    artifact_svc: ArtifactService = Depends(get_artifact_service),
) -> ArtifactResponse:
    """Write a new version; the previous content is snapshotted (never lost)."""
    artifact = await artifact_svc.add_version(artifact_id, current_user.id, body)
    return ArtifactResponse.model_validate(artifact)


@router.delete("/{artifact_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_artifact(
    artifact_id: UUID,
    current_user: User = Depends(get_current_user),
    artifact_svc: ArtifactService = Depends(get_artifact_service),
) -> None:
    """Delete the artifact and every version snapshot."""
    await artifact_svc.delete(artifact_id, current_user.id)
