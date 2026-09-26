"""
Admin Management Endpoints.
Provides user management, system health overview, audit logs, and global configuration.
"""
from typing import Any
from uuid import UUID

import structlog
from backend.app.api.deps import get_current_admin, get_db
from backend.app.domain.system.service import SystemService
from backend.app.domain.user.models import User
from backend.app.infrastructure.cache.redis_client import redis_client
from backend.app.infrastructure.database.engine import check_database_health
from backend.app.services.monitoring.drift_service import DriftService
from backend.app.services.observability.metrics import metrics_collector
from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/admin", tags=["admin"])


@router.get("/users")
async def list_users(
    session: AsyncSession = Depends(get_db),
    admin: User = Depends(get_current_admin),
) -> list[dict[str, Any]]:
    """List all registered users."""
    stmt = select(User).order_by(User.created_at.desc())
    res = await session.exec(stmt)
    users = res.all()
    return [
        {
            "id": str(u.id),
            "email": u.email,
            "username": u.username,
            "full_name": u.full_name,
            "role": u.role,
            "is_active": u.is_active,
            "is_verified": u.is_verified,
            "created_at": u.created_at.isoformat() if u.created_at else None,
        }
        for u in users
    ]


@router.post("/users/{user_id}/toggle-status")
async def toggle_user_status(
    user_id: UUID,
    session: AsyncSession = Depends(get_db),
    admin: User = Depends(get_current_admin),
) -> dict[str, Any]:
    """Enable or disable a user account."""
    user = await session.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    if user.id == admin.id:
        raise HTTPException(status_code=400, detail="Cannot disable your own admin account")

    user.is_active = not user.is_active
    session.add(user)
    await session.commit()
    return {"user_id": str(user.id), "is_active": user.is_active}


@router.get("/audit-logs")
async def get_audit_logs(
    limit: int = 50,
    session: AsyncSession = Depends(get_db),
    admin: User = Depends(get_current_admin),
) -> list[dict[str, Any]]:
    """Retrieve compliance audit logs."""
    system_service = SystemService(session)
    logs = await system_service.get_audit_logs(limit=limit)
    return [
        {
            "id": str(log.id),
            "user_id": str(log.user_id) if log.user_id else None,
            "action": log.action,
            "resource_type": log.resource_type,
            "status": log.status,
            "ip_address": log.ip_address,
            "created_at": log.created_at.isoformat() if log.created_at else None,
        }
        for log in logs
    ]


@router.get("/system-status")
async def get_system_status(
    session: AsyncSession = Depends(get_db),
    admin: User = Depends(get_current_admin),
) -> dict[str, Any]:
    """Retrieve live health and metrics across database, cache, and services."""
    db_healthy = await check_database_health()
    redis_healthy = await redis_client.ping()

    telemetry = metrics_collector.get_summary()

    return {
        "status": "healthy" if db_healthy and redis_healthy else "degraded",
        "database": "connected" if db_healthy else "disconnected",
        "redis_cache": "connected" if redis_healthy else "disconnected",
        "telemetry": telemetry,
    }


@router.get("/monitoring/drift")
async def get_drift_report(
    session: AsyncSession = Depends(get_db),
    admin: User = Depends(get_current_admin),
) -> dict[str, Any]:
    """
    LLM-era drift report (MD §7.6/§8.8): sliding-window z-score comparison of
    recent vs immediately-preceding telemetry for latency, cost, error rate,
    faithfulness-pass rate and hallucination rate. ``detected=true`` means one
    or more metrics moved >2σ from their baseline window.
    """
    service = DriftService(session)
    try:
        return await service.drift_report()
    except Exception as exc:
        logger.warning("drift_report_query_failed", error=str(exc))
        return {
            "detected": False,
            "drifting_metrics": 0,
            "detail": {},
            "error": f"Drift report unavailable: {exc}",
        }
