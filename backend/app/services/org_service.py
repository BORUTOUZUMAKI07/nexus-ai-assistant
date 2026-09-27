"""
Organization / workspace service (multi-tenant layer).

Users create orgs, invite colleagues by email, and roles gate management
(owner > admin > member). Companionship with conversations is intentionally
additive: orgs do not alter existing user-owned conversation queries, so the
established IDOR guarantees and the live sweep hold unchanged.
"""
import secrets
import re
from datetime import UTC, datetime, timedelta
from uuid import UUID

import structlog
from backend.app.core.config import settings
from backend.app.domain.org.models import Organization, OrganizationInvite, OrganizationMember
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

logger = structlog.get_logger(__name__)

MANAGE_ROLES = {"owner", "admin"}


class OrganizationService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    @staticmethod
    def _slugify(name: str) -> str:
        slug = re.sub(r"[^a-z0-9-]", "-", name.lower().strip())
        slug = re.sub(r"-+", "-", slug).strip("-")
        return slug[:48] or "org"

    async def create(self, user_id: UUID, name: str, slug: str | None = None) -> Organization:
        slug = slug or self._slugify(name)
        existing = await self.session.exec(select(Organization).where(Organization.slug == slug))
        if existing.first():
            raise ValueError(f"An organization with slug '{slug}' already exists.")
        org = Organization(name=name, slug=slug, owner_id=user_id)
        self.session.add(org)
        await self.session.commit()
        await self.session.refresh(org)
        member = OrganizationMember(organization_id=org.id, user_id=user_id, role="owner")
        self.session.add(member)
        await self.session.commit()
        logger.info("organization_created", org_id=str(org.id), owner_id=str(user_id))
        return org

    async def list_my(self, user_id: UUID) -> list[dict]:
        rows = (await self.session.exec(
            select(Organization, OrganizationMember.role)
            .join(OrganizationMember, OrganizationMember.organization_id == Organization.id)
            .where(OrganizationMember.user_id == user_id)
            .order_by(Organization.created_at.desc())
        )).all()
        return [
            {
                "id": str(org.id),
                "name": org.name,
                "slug": org.slug,
                "description": org.description,
                "role": role,
                "owner_id": str(org.owner_id),
                "created_at": org.created_at.isoformat() if org.created_at else None,
            }
            for org, role in rows
        ]

    async def _org(self, org_id: UUID) -> Organization | None:
        return await self.session.get(Organization, org_id)

    async def _role(self, org_id: UUID, user_id: UUID) -> str | None:
        res = await self.session.exec(
            select(OrganizationMember).where(
                OrganizationMember.organization_id == org_id,
                OrganizationMember.user_id == user_id,
            )
        )
        member = res.first()
        return member.role if member else None

    async def get_for_member(self, org_id: UUID, user_id: UUID) -> dict | None:
        org = await self._org(org_id)
        if not org or await self._role(org_id, user_id) is None:
            return None
        return {
            "id": str(org.id),
            "name": org.name,
            "slug": org.slug,
            "description": org.description,
            "owner_id": str(org.owner_id),
            "member_count": len(await self.list_members(org_id)),
            "created_at": org.created_at.isoformat() if org.created_at else None,
        }

    async def list_members(self, org_id: UUID) -> list[dict]:
        rows = (await self.session.exec(
            select(OrganizationMember, OrganizationMember.role)
            .where(OrganizationMember.organization_id == org_id)
            .order_by(OrganizationMember.created_at.asc())
        )).all()
        members = []
        from backend.app.domain.user.models import User
        for member, _ in rows:
            user = await self.session.get(User, member.user_id)
            members.append(
                {
                    "user_id": str(member.user_id),
                    "email": user.email if user else None,
                    "role": member.role,
                    "joined_at": member.created_at.isoformat() if member.created_at else None,
                }
            )
        return members

    async def invite(
        self, org_id: UUID, actor_id: UUID, email: str, role: str = "member"
    ) -> OrganizationInvite | None:
        org = await self._org(org_id)
        if not org:
            return None
        actor_role = await self._role(org_id, actor_id)
        if actor_role not in MANAGE_ROLES:
            raise PermissionError("Only owners and admins can invite members.")
        role = role if role in {"admin", "member"} else "member"
        existing = await self.session.exec(
            select(OrganizationInvite).where(
                OrganizationInvite.organization_id == org_id,
                OrganizationInvite.email == email.lower().strip(),
                OrganizationInvite.status == "pending",
            )
        )
        if existing.first():
            raise ValueError("An invite is already pending for this email.")
        invite = OrganizationInvite(
            organization_id=org_id,
            invited_by=actor_id,
            email=email.lower().strip(),
            role=role,
            token=secrets.token_urlsafe(24),
            expires_at=datetime.now(UTC).replace(tzinfo=None) + timedelta(days=7),
        )
        self.session.add(invite)
        await self.session.commit()
        await self.session.refresh(invite)
        logger.info("organization_invite_created", org_id=str(org_id), email=invite.email)
        return invite

    async def accept_invite(self, token: str, user_id: UUID) -> Organization | None:
        res = await self.session.exec(select(OrganizationInvite).where(OrganizationInvite.token == token))
        invite = res.first()
        if not invite or invite.status != "pending":
            return None
        if invite.expires_at < datetime.now(UTC).replace(tzinfo=None):
            return None
        from backend.app.domain.user.models import User as NexusUser
        user = await self.session.get(NexusUser, user_id)
        if not user or user.email.lower() != invite.email.lower():
            raise PermissionError("This invite is addressed to a different email address.")
        member = OrganizationMember(
            organization_id=invite.organization_id, user_id=user_id, role=invite.role
        )
        self.session.add(member)
        invite.status = "accepted"
        self.session.add(invite)
        await self.session.commit()
        return await self._org(invite.organization_id)

    async def remove_member(self, org_id: UUID, actor_id: UUID, target_user_id: UUID) -> bool:
        org = await self._org(org_id)
        if not org:
            return False
        actor_role = await self._role(org_id, actor_id)
        if actor_role not in MANAGE_ROLES:
            raise PermissionError("Only owners and admins can remove members.")
        if target_user_id == org.owner_id:
            raise ValueError("The owner cannot be removed from their own organization.")
        res = await self.session.exec(
            select(OrganizationMember).where(
                OrganizationMember.organization_id == org_id,
                OrganizationMember.user_id == target_user_id,
            )
        )
        member = res.first()
        if not member:
            return False
        await self.session.delete(member)
        await self.session.commit()
        return True

    async def leave(self, org_id: UUID, user_id: UUID) -> bool:
        org = await self._org(org_id)
        if not org:
            return False
        if org.owner_id == user_id:
            raise ValueError("The owner must transfer or delete the organization instead of leaving.")
        res = await self.session.exec(
            select(OrganizationMember).where(
                OrganizationMember.organization_id == org_id,
                OrganizationMember.user_id == user_id,
            )
        )
        member = res.first()
        if not member:
            return False
        await self.session.delete(member)
        await self.session.commit()
        return True

    async def delete(self, org_id: UUID, actor_id: UUID) -> bool:
        org = await self._org(org_id)
        if not org:
            return False
        if org.owner_id != actor_id:
            raise PermissionError("Only the owner can delete an organization.")
        from sqlmodel import delete as sq_delete
        await self.session.exec(sq_delete(OrganizationMember).where(OrganizationMember.organization_id == org_id))
        await self.session.exec(sq_delete(OrganizationInvite).where(OrganizationInvite.organization_id == org_id))
        await self.session.delete(org)
        await self.session.commit()
        return True