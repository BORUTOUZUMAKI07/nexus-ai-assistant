"""
System Domain Service.
Handles system configuration keys, audit logging, and global platform status.
"""
from typing import Any
from uuid import UUID

import structlog
from backend.app.domain.system.models import AuditLog, SystemConfig
from backend.app.domain.system.repository import SystemRepository
from backend.app.domain.system.schemas import AuditLogCreate
from sqlmodel.ext.asyncio.session import AsyncSession

logger = structlog.get_logger(__name__)


class SystemService:
    """Business operations for system configuration and compliance audit trail."""

    def __init__(self, session: AsyncSession) -> None:
        self.repo = SystemRepository(session)

    async def get_config(self, key: str, default: Any | None = None) -> Any:
        cfg = await self.repo.get_config(key)
        if cfg:
            return cfg.value_json
        return default

    async def set_config(
        self,
        key: str,
        value_json: dict[str, Any],
        category: str = "general",
        description: str | None = None,
        is_secret: bool = False,
        updated_by: UUID | None = None,
    ) -> SystemConfig:
        return await self.repo.set_config(
            key=key,
            value=value_json,
            category=category,
            description=description,
            is_secret=is_secret,
            updated_by=updated_by,
        )

    async def log_audit(
        self,
        action: str,
        resource_type: str,
        user_id: UUID | None = None,
        resource_id: str | None = None,
        ip_address: str | None = None,
        user_agent: str | None = None,
        status: str = "success",
        details: dict[str, Any] | None = None,
    ) -> AuditLog:
        audit_in = AuditLogCreate(
            user_id=user_id,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            ip_address=ip_address,
            user_agent=user_agent,
            status=status,
            details=details,
        )
        return await self.repo.log_audit(audit_in)

    async def get_audit_logs(
        self, user_id: UUID | None = None, limit: int = 100, offset: int = 0
    ) -> list[AuditLog]:
        return await self.repo.get_audit_logs(limit=limit, user_id=user_id)
