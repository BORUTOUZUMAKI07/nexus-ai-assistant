"""
Account lifecycle service — GDPR data export and right-to-erasure.

`export_user_data` returns a portable JSON snapshot of everything the user owns.
`delete_account` performs a complete, dependency-ordered cascade so the users
row is never blocked by a NO-ACTION foreign key.
"""
import json
from datetime import UTC, datetime
from uuid import UUID

import structlog
from backend.app.domain.artifact.models import Artifact, ArtifactVersion
from backend.app.domain.conversation.models import Conversation, Message
from backend.app.domain.conversation.repository import ConversationRepository
from backend.app.domain.file.models import File, FileChunk, FileMetadata
from backend.app.domain.hook.models import HookPolicy
from backend.app.domain.org.models import Organization, OrganizationInvite, OrganizationMember
from backend.app.domain.plan.models import Plan
from backend.app.domain.prompt.models import PromptTemplate, PromptVersion
from backend.app.domain.share.models import ConversationShare
from backend.app.domain.tool.models import ToolCall, ToolPermission
from backend.app.domain.usage.models import CostLog, UsageLog
from backend.app.domain.user.models import APIKey, User, UserMemory, UserSettings
from backend.app.domain.webhook.models import WebhookDelivery, WebhookEndpoint
from backend.app.infrastructure.storage.supabase_storage import storage_client
from backend.app.infrastructure.vector.qdrant_client import vector_db
from sqlmodel import delete, select
from sqlmodel.ext.asyncio.session import AsyncSession

logger = structlog.get_logger(__name__)


class AccountService:
    def __init__(
        self,
        session: AsyncSession,
        vector_store=None,
        storage=None,
    ) -> None:
        self.session = session
        # Cross-store deletes (HLD: saga/reconciliation) are injected so tests
        # can substitute fakes — default to the live Qdrant/Supabase clients.
        self._vector_store = vector_store if vector_store is not None else vector_db
        self._storage = storage if storage is not None else storage_client

    async def export_user_data(self, user_id: UUID) -> dict:
        """Fully portable GDPR export of the user's data (machines + humans)."""
        user = await self.session.get(User, user_id)
        if not user:
            raise ValueError("User not found")

        settings_row = await self.session.exec(
            select(UserSettings).where(UserSettings.user_id == user_id)
        )
        settings_row = settings_row.first()

        conversations = (await self.session.exec(
            select(Conversation).where(Conversation.user_id == user_id)
        )).all()
        conv_payload = []
        for conv in conversations:
            messages = (await self.session.exec(
                select(Message)
                .where(Message.conversation_id == conv.id)
                .order_by(Message.created_at.asc())
            )).all()
            conv_payload.append(
                {
                    "conversation_id": str(conv.id),
                    "title": conv.title,
                    "model": conv.model,
                    "created_at": conv.created_at.isoformat() if conv.created_at else None,
                    "messages": [
                        {
                            "role": m.role,
                            "content": m.content,
                            "model": m.model,
                            "citations": m.citations or [],
                            "feedback": m.user_feedback,
                            "created_at": m.created_at.isoformat() if m.created_at else None,
                        }
                        for m in messages
                    ],
                }
            )

        files = (await self.session.exec(
            select(File).where(File.user_id == user_id)
        )).all()

        memories = (await self.session.exec(
            select(UserMemory).where(UserMemory.user_id == user_id)
        )).all()

        api_keys = (await self.session.exec(
            select(APIKey).where(APIKey.user_id == user_id)
        )).all()

        usage = (await self.session.exec(
            select(UsageLog).where(UsageLog.user_id == user_id)
        )).all()

        return {
            "schema_version": "1.0",
            "exported_at": datetime.now(UTC).isoformat(),
            "user": {
                "id": str(user.id),
                "email": user.email,
                "username": user.username,
                "full_name": user.full_name,
                "role": user.role,
                "created_at": user.created_at.isoformat() if user.created_at else None,
            },
            "settings": {
                "default_model": settings_row.default_model if settings_row else None,
                "theme": settings_row.theme if settings_row else None,
                "language": settings_row.language if settings_row else None,
                "system_prompt_override": getattr(settings_row, "system_prompt_override", None),
                "memory_enabled": getattr(settings_row, "enable_memory", None),
                "tools_enabled": getattr(settings_row, "enable_tools", None),
            },
            "conversations": conv_payload,
            "files": [
                {
                    "id": str(f.id),
                    "filename": f.filename,
                    "original_filename": f.original_filename,
                    "file_type": f.file_type,
                    "size_bytes": f.size_bytes,
                    "status": f.status,
                    "created_at": f.created_at.isoformat() if f.created_at else None,
                }
                for f in files
            ],
            "memories": [
                {
                    "id": str(m.id),
                    "category": m.category,
                    "content": m.content,
                    "confidence": m.confidence,
                    "active": m.is_active,
                    "created_at": m.created_at.isoformat() if m.created_at else None,
                }
                for m in memories
            ],
            "api_keys": [
                {
                    "provider": k.provider,
                    "key_preview": k.key_preview,
                    "label": k.label,
                    "active": k.is_active,
                }
                for k in api_keys
            ],
            "usage_summary": {
                "calls": len(usage),
                "total_tokens": sum(u.total_tokens for u in usage),
                "total_cost_usd": round(sum(u.cost_usd for u in usage), 6),
            },
        }

    async def delete_account(self, user_id: UUID) -> None:
        """Dependency-ordered erasure of every row owned by the user."""
        user = await self.session.get(User, user_id)
        if not user:
            raise ValueError("User not found")

        # Crossing the service boundary (HLD: saga/reconciliation): snapshot
        # every blob the user owns BEFORE the DB cascade deletes their rows.
        # The DB is the source of truth for the erasure; vector/storage cleanup
        # below is best-effort fail-open so an infrastructure outage can never
        # block right-to-erasure, and the reconciliation sweep retries stragglers.
        user_files = (await self.session.exec(
            select(File).where(File.user_id == user_id)
        )).all()
        storage_paths = [f.storage_path for f in user_files if f.storage_path]

        # 1. Conversations (handles branches, attachments, tool calls, messages,
        #    conversation-scoped files + chunks + usage in one cascade).
        conversations = (await self.session.exec(
            select(Conversation).where(Conversation.user_id == user_id)
        )).all()
        conv_repo = ConversationRepository(self.session)
        for conv in conversations:
            await conv_repo.delete(conv)

        # 2. Remaining user-scoped files (conversation_id NULL) + their children.
        file_ids = select(File.id).where(File.user_id == user_id)
        await self.session.exec(delete(FileChunk).where(FileChunk.file_id.in_(file_ids)))
        await self.session.exec(delete(FileMetadata).where(FileMetadata.file_id.in_(file_ids)))
        await self.session.exec(delete(File).where(File.user_id == user_id))

        # 3. Tool rows (tool-call approval claims + permissions). ToolCalls are
        #    conversation-scoped (no user FK), so target them by conversation.
        conversation_ids = [c.id for c in conversations]
        await self.session.exec(delete(ToolCall).where(ToolCall.conversation_id.in_(conversation_ids)))
        await self.session.exec(delete(ToolPermission).where(ToolPermission.user_id == user_id))

        # 3b. Plans + artifacts the user owns. Conversation-scoped plans/artifacts
        #     were already removed by the conversation cascade above; these deletes
        #     clear everything else (e.g. orphaned/standalone artifacts) so the
        #     erasure is complete.
        await self.session.exec(delete(Plan).where(Plan.user_id == user_id))
        artifact_ids = select(Artifact.id).where(Artifact.user_id == user_id)
        await self.session.exec(delete(ArtifactVersion).where(ArtifactVersion.artifact_id.in_(artifact_ids)))
        await self.session.exec(delete(Artifact).where(Artifact.user_id == user_id))

        # 3c. Org-scoped hook policies referencing any org the user owned.
        org_ids = select(Organization.id).where(Organization.owner_id == user_id)
        await self.session.exec(delete(HookPolicy).where(HookPolicy.org_id.in_(org_ids)))

        # 4. Usage + cost telemetry.
        await self.session.exec(delete(UsageLog).where(UsageLog.user_id == user_id))
        await self.session.exec(delete(CostLog).where(CostLog.user_id == user_id))

        # 5. Prompt templates + versions authored by the user.
        template_ids = select(PromptTemplate.id).where(PromptTemplate.user_id == user_id)
        await self.session.exec(delete(PromptVersion).where(PromptVersion.template_id.in_(template_ids)))
        await self.session.exec(delete(PromptVersion).where(PromptVersion.created_by == user_id))
        await self.session.exec(delete(PromptTemplate).where(PromptTemplate.user_id == user_id))

        # 6. User-scoped rows.
        await self.session.exec(delete(UserMemory).where(UserMemory.user_id == user_id))
        await self.session.exec(delete(APIKey).where(APIKey.user_id == user_id))
        await self.session.exec(delete(UserSettings).where(UserSettings.user_id == user_id))

        # 7. Webhooks (deliveries first — FK → endpoint).
        endpoint_ids = select(WebhookEndpoint.id).where(WebhookEndpoint.user_id == user_id)
        await self.session.exec(delete(WebhookDelivery).where(WebhookDelivery.endpoint_id.in_(endpoint_ids)))
        await self.session.exec(delete(WebhookEndpoint).where(WebhookEndpoint.user_id == user_id))

        # 8. Shares owned by the user.
        await self.session.exec(delete(ConversationShare).where(ConversationShare.created_by == user_id))

        # 9. Organizations: memberships, invites authored by the user, then any
        #    orgs the user owns (their members + invites must go first).
        member_org_ids = select(OrganizationMember.organization_id).where(OrganizationMember.user_id == user_id)
        owner_org_ids = select(Organization.id).where(Organization.owner_id == user_id)
        await self.session.exec(
            delete(OrganizationMember).where((OrganizationMember.organization_id.in_(member_org_ids)) | (OrganizationMember.organization_id.in_(owner_org_ids)))
        )
        await self.session.exec(
            delete(OrganizationInvite).where(
                (OrganizationInvite.invited_by == user_id) | (OrganizationInvite.organization_id.in_(owner_org_ids))
            )
        )
        await self.session.exec(delete(Organization).where(Organization.owner_id == user_id))

        # 10. The user row itself.
        await self.session.delete(user)
        await self.session.commit()

        # 11. Cross-store erasure (fail-open saga step): purge the user's
        #     Qdrant vectors and object-storage blobs. Failures are logged, never
        #     raised — GDPR erasure already succeeded in the DB; the periodic
        #     reconciliation sweep re-attempts anything left behind.
        try:
            await self._vector_store.delete_by_filter({"user_id": str(user_id)})
        except Exception as exc:
            logger.warning("gdpr_vector_purge_failed", user_id=str(user_id), error=str(exc))
        for path in storage_paths:
            try:
                await self._storage.delete(path)
            except Exception as exc:
                logger.warning("gdpr_storage_delete_failed", user_id=str(user_id), path=path, error=str(exc))

        logger.info("account_deleted", user_id=str(user_id))

    async def export_as_json_bytes(self, user_id: UUID) -> bytes:
        payload = await self.export_user_data(user_id)
        return json.dumps(payload, indent=2, default=str).encode("utf-8")
