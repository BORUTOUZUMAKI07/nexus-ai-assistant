"""
Organizations / workspaces API (multi-tenant layer).

members can view their orgs; owners/admins manage invites and memberships.
"""
from uuid import UUID

import structlog
from backend.app.api.deps import get_current_user, get_org_service
from backend.app.core.config import settings
from backend.app.domain.user.models import User
from backend.app.services.org_service import OrganizationService
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, EmailStr, Field

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/orgs", tags=["organizations"])


class OrgCreate(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    slug: str | None = None
    description: str | None = None


class OrgUpdate(BaseModel):
    name: str | None = None
    description: str | None = None


class InviteCreate(BaseModel):
    email: EmailStr
    role: str = "member"


class InviteAccept(BaseModel):
    token: str


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_org(
    body: OrgCreate,
    current_user: User = Depends(get_current_user),
    org_svc: OrganizationService = Depends(get_org_service),
):
    try:
        org = await org_svc.create(current_user.id, body.name, body.slug)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return {
        "id": str(org.id),
        "name": org.name,
        "slug": org.slug,
        "owner_id": str(org.owner_id),
        "created_at": org.created_at.isoformat() if org.created_at else None,
    }


@router.get("")
async def list_my_orgs(
    current_user: User = Depends(get_current_user),
    org_svc: OrganizationService = Depends(get_org_service),
):
    return await org_svc.list_my(current_user.id)


@router.get("/{org_id}")
async def get_org(
    org_id: UUID,
    current_user: User = Depends(get_current_user),
    org_svc: OrganizationService = Depends(get_org_service),
):
    org = await org_svc.get_for_member(org_id, current_user.id)
    if not org:
        raise HTTPException(status_code=404, detail="Organization not found")
    return org


@router.get("/{org_id}/members")
async def list_members(
    org_id: UUID,
    current_user: User = Depends(get_current_user),
    org_svc: OrganizationService = Depends(get_org_service),
):
    if await org_svc.get_for_member(org_id, current_user.id) is None:
        raise HTTPException(status_code=403, detail="Not a member of this organization")
    return await org_svc.list_members(org_id)


@router.post("/{org_id}/invites", status_code=status.HTTP_201_CREATED)
async def invite_member(
    org_id: UUID,
    body: InviteCreate,
    current_user: User = Depends(get_current_user),
    org_svc: OrganizationService = Depends(get_org_service),
):
    """Owner/admin invites a colleague by email. Returns the invite (with a dev accept link)."""
    try:
        invite = await org_svc.invite(org_id, current_user.id, body.email, body.role)
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    if not invite:
        raise HTTPException(status_code=404, detail="Organization not found")
    result = {
        "id": str(invite.id),
        "organization_id": str(invite.organization_id),
        "email": invite.email,
        "role": invite.role,
        "status": invite.status,
        "expires_at": invite.expires_at.isoformat() if invite.expires_at else None,
    }
    result["dev_accept_url"] = (
        f"{settings.APP_PUBLIC_URL}/invite?token={invite.token}"
    )
    return result


@router.post("/invites/accept")
async def accept_invite(
    body: InviteAccept,
    current_user: User = Depends(get_current_user),
    org_svc: OrganizationService = Depends(get_org_service),
):
    try:
        org = await org_svc.accept_invite(body.token, current_user.id)
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc))
    if not org:
        raise HTTPException(status_code=400, detail="Invite is invalid, expired, or already used")
    return {"status": "joined", "organization_id": str(org.id), "name": org.name}


@router.delete("/{org_id}/members/{user_id}")
async def remove_member(
    org_id: UUID,
    user_id: UUID,
    current_user: User = Depends(get_current_user),
    org_svc: OrganizationService = Depends(get_org_service),
):
    try:
        ok = await org_svc.remove_member(org_id, current_user.id, user_id)
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    if not ok:
        raise HTTPException(status_code=404, detail="Member or organization not found")
    return {"status": "removed"}


@router.post("/{org_id}/leave")
async def leave_org(
    org_id: UUID,
    current_user: User = Depends(get_current_user),
    org_svc: OrganizationService = Depends(get_org_service),
):
    try:
        ok = await org_svc.leave(org_id, current_user.id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    if not ok:
        raise HTTPException(status_code=404, detail="Organization not found")
    return {"status": "left"}


@router.delete("/{org_id}", status_code=status.HTTP_200_OK)
async def delete_org(
    org_id: UUID,
    current_user: User = Depends(get_current_user),
    org_svc: OrganizationService = Depends(get_org_service),
):
    try:
        ok = await org_svc.delete(org_id, current_user.id)
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc))
    if not ok:
        raise HTTPException(status_code=404, detail="Organization not found")
    return {"status": "deleted"}