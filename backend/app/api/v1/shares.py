"""
Read-only conversation sharing API.

Owner side: create/list/revoke share links (auth required).
Public side: GET /public/shares/{token} — unauthenticated, content only.
"""
from uuid import UUID

import structlog
from backend.app.api.deps import get_current_user, get_share_service
from backend.app.core.config import settings
from backend.app.domain.user.models import User
from backend.app.services.share_service import ShareService
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/shares", tags=["shares"])

public_router = APIRouter(prefix="/public/shares", tags=["shares"], include_in_schema=False)


class ShareCreate(BaseModel):
    ttl_seconds: int | None = None


@router.post("/{conversation_id}", status_code=status.HTTP_201_CREATED)
async def create_share_link(
    conversation_id: UUID,
    body: ShareCreate,
    current_user: User = Depends(get_current_user),
    share_svc: ShareService = Depends(get_share_service),
):
    """Mint a revocable read-only link for one of the user's own conversations."""
    ttl = body.ttl_seconds if body.ttl_seconds is not None else settings.SHARE_DEFAULT_TTL_SECONDS
    try:
        share = await share_svc.create(current_user.id, conversation_id, ttl_seconds=ttl)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return {
        "token": share.token,
        "url": f"{settings.APP_PUBLIC_URL}/share/{share.token}",
        "expires_at": share.expires_at.isoformat() if share.expires_at else None,
    }


@router.get("/{conversation_id}")
async def list_share_links(
    conversation_id: UUID,
    current_user: User = Depends(get_current_user),
    share_svc: ShareService = Depends(get_share_service),
):
    shares = await share_svc.list_for_conversation(current_user.id, conversation_id)
    return [
        {
            "token": s.token,
            "url": f"{settings.APP_PUBLIC_URL}/share/{s.token}",
            "expires_at": s.expires_at.isoformat() if s.expires_at else None,
        }
        for s in shares
    ]


@router.delete("/{conversation_id}", status_code=status.HTTP_200_OK)
async def revoke_share_links(
    conversation_id: UUID,
    current_user: User = Depends(get_current_user),
    share_svc: ShareService = Depends(get_share_service),
):
    ok = await share_svc.revoke(current_user.id, conversation_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return {"status": "revoked"}


# ── Public (unauthenticated) read ────────────────────────────────────────────

@public_router.get("/{token}")
async def read_public_share(
    token: str,
    share_svc: ShareService = Depends(get_share_service),
):
    """Non-authenticated read-only view of a shared conversation (by token)."""
    payload = await share_svc.read_public(token)
    if not payload:
        raise HTTPException(status_code=404, detail="Share link not found or expired")
    return payload
