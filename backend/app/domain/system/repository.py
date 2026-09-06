"""
Repository for System and Audit domain operations.
"""
from datetime import datetime
from uuid import UUID

from backend.app.domain.base_repository import BaseRepository
from backend.app.domain.system.models import AuditLog, SystemConfig
from backend.app.domain.system.schemas import AuditLogCreate
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession


class SystemRepository(BaseRepository[SystemConfig]):
    def __init__(self, session: AsyncSession):
        super().__init__(session, SystemConfig)

    async def get_config(self, key: str) -> SystemConfig | None:
        statement = select(SystemConfig).where(SystemConfig.key == key)
        result = await self.session.exec(statement)
        return result.first()

    async def set_config(self, key: str, value: dict, category: str = "general", description: str | None = None, is_secret: bool = False, updated_by: UUID | None = None) -> SystemConfig:
        config = await self.get_config(key)
        if config:
            config.value_json = value
            config.category = category
            if description:
                config.description = description
            config.is_secret = is_secret
            config.updated_by = updated_by
            config.updated_at = datetime.utcnow()
            self.session.add(config)
        else:
            config = SystemConfig(
                key=key,
                value_json=value,
                category=category,
                description=description,
                is_secret=is_secret,
                updated_by=updated_by,
            )
            self.session.add(config)
        await self.session.commit()
        await self.session.refresh(config)
        return config

    async def log_audit(self, audit_in: AuditLogCreate) -> AuditLog:
        db_audit = AuditLog(
            user_id=audit_in.user_id,
            action=audit_in.action,
            resource_type=audit_in.resource_type,
            resource_id=audit_in.resource_id,
            ip_address=audit_in.ip_address,
            user_agent=audit_in.user_agent,
            status=audit_in.status,
            details=audit_in.details,
        )
        self.session.add(db_audit)
        await self.session.commit()
        await self.session.refresh(db_audit)
        return db_audit

    async def get_audit_logs(self, limit: int = 100, user_id: UUID | None = None) -> list[AuditLog]:
        statement = select(AuditLog)
        if user_id:
            statement = statement.where(AuditLog.user_id == user_id)
        statement = statement.order_by(AuditLog.created_at.desc()).limit(limit)
        result = await self.session.exec(statement)
        return list(result.all())


# Domain-specific repository aliases
SystemConfigRepository = SystemRepository
AuditLogRepository = SystemRepository
