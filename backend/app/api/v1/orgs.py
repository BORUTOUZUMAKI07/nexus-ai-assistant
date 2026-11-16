"""
Organizations / workspaces API (multi-tenant layer).

members can view their orgs; owners/admins manage invites and memberships.
"""
from uuid import UUID

import structlog
from backend.app.api.deps import get_current_user, get_org_service, get_usage_service
from backend.app.core.config import settings
from backend.app.domain.usage.schemas import OrgUsageSummaryResponse
from backend.app.domain.user.models import User
from backend.app.services.notifications.email import email_service, escape_html
from backend.app.services.orgs.service import OrganizationService
from backend.app.services.usage.service import UsageService
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, EmailStr, Field

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/orgs", tags=["organizations"])


class OrgCreate(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    slug: str | None = None
    description: str | None = None


class OrgUpdate(BaseModel):
    # Both optional: a PATCH that renames does not have to resend the description
    # (and vice versa). An explicit null clears the description.
    name: str | None = Field(default=None, min_length=2, max_length=120)
    description: str | None = Field(default=None, max_length=2000)


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


@router.patch("/{org_id}")
async def update_org(
    org_id: UUID,
    body: OrgUpdate,
    current_user: User = Depends(get_current_user),
    org_svc: OrganizationService = Depends(get_org_service),
):
    """Rename / re-describe an organization (owner or admin)."""
    try:
        org = await org_svc.update(
            org_id, current_user.id, name=body.name, description=body.description
        )
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    if not org:
        raise HTTPException(status_code=404, detail="Organization not found")
    return {
        "id": str(org.id),
        "name": org.name,
        "slug": org.slug,
        "description": org.description,
        "owner_id": str(org.owner_id),
        "updated_at": org.updated_at.isoformat() if org.updated_at else None,
    }


@router.get("/{org_id}/invites")
async def list_org_invites(
    org_id: UUID,
    current_user: User = Depends(get_current_user),
    org_svc: OrganizationService = Depends(get_org_service),
):
    """Pending/accepted invites for an organization (owner or admin only)."""
    try:
        invites = await org_svc.list_invites(org_id, current_user.id)
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc))
    if invites is None:
        raise HTTPException(status_code=404, detail="Organization not found")
    return invites


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
    """Owner/admin invites a colleague by email.

    Returns the invite plus the accept link. When a mail provider is configured
    the same link is emailed; when it is not, the response link is the only
    delivery path and the UI shows it for the owner to copy.
    """
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
    # APP_PUBLIC_URL may carry a trailing slash; joining naively produced
    # "...vercel.app//invite?token=..." in the live accept link.
    accept_url = f"{settings.APP_PUBLIC_URL.rstrip('/')}/invite?token={invite.token}"
    result["dev_accept_url"] = accept_url
    org_meta = await org_svc.get_for_member(org_id, current_user.id) or {}
    org_name = str(org_meta.get("name") or "the organization")

    # Actually send the invite when a mail provider is configured.
    #
    # The email service existed but nothing on the invite path ever called it, so
    # an invited colleague received nothing and the owner had to hand over the
    # link manually. `send` is fail-open and returns `channel="dev"` when no
    # provider is configured, in which case the link in the response is the only
    # delivery path — which is exactly what the UI shows.
    try:
        delivery = await email_service.send(
            to=invite.email,
            subject=f"You have been invited to join {org_name}",
            body_html=(
                f"<p>{escape_html(current_user.full_name or current_user.username)} "
                f"invited you to <strong>{escape_html(org_name)}</strong> on "
                f"{escape_html(settings.PROJECT_NAME)}.</p>"
                f'<p><a href="{escape_html(accept_url)}">Accept the invitation</a></p>'
                f"<p>This link expires {escape_html(result['expires_at'] or 'in 7 days')}.</p>"
            ),
            body_text=(
                f"You have been invited to join {org_name}.\n\n"
                f"Accept: {accept_url}\n"
            ),
        )
        # The delivery outcome travels with the invite: `failed` means a provider
        # is configured but rejected the message, `dev` means none is configured.
        # Both are states the owner has to see, because in both cases the invite
        # was NOT delivered and the accept link below is the only way in.
        result["email_sent"] = bool(delivery.get("sent"))
        result["email_channel"] = delivery.get("channel")
        result["email_message"] = delivery.get("message")
    except Exception as exc:  # never fail the invite on a mail outage
        logger.warning("org_invite_email_failed", error=type(exc).__name__)
        result["email_sent"] = False
        result["email_channel"] = "failed"
        result["email_message"] = "Email delivery failed."

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


@router.get("/{org_id}/usage-summary", response_model=OrgUsageSummaryResponse)
async def org_usage_summary(
    org_id: UUID,
    billing_period: str | None = None,
    current_user: User = Depends(get_current_user),
    org_svc: OrganizationService = Depends(get_org_service),
    usage_svc: UsageService = Depends(get_usage_service),
):
    """Per-org usage + cost rollup — any member can view (T-07 multi-tenant
    observability). ``billing_period`` (YYYY-MM) filters the cost leg; usage is
    all-time. 404 for non-members keeps org existence private."""
    if await org_svc.get_for_member(org_id, current_user.id) is None:
        raise HTTPException(status_code=404, detail="Organization not found")
    return await usage_svc.get_org_summary(org_id, billing_period=billing_period)


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
