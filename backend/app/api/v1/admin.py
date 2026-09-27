"""
Admin Management Endpoints.
Provides user management, system health overview, audit logs, and global configuration.
"""
from typing import Any
from uuid import UUID

import structlog
from backend.app.api.deps import get_current_admin, get_db
from backend.app.domain.hook.schemas import HookPolicyCreate, HookPolicyUpdate
from backend.app.domain.system.service import SystemService
from backend.app.domain.usage.models import CostLog
from backend.app.domain.user.models import User
from backend.app.infrastructure.cache.redis_client import redis_client
from backend.app.infrastructure.database.engine import check_database_health
from backend.app.services.hook_service import HookService
from backend.app.services.monitoring.drift_service import DriftService
from backend.app.services.observability.metrics import metrics_collector
from backend.app.services.observability.viewer import (
    gather_viewer_data,
    observability_stack_status,
    render_viewer_html,
)
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import HTMLResponse
from sqlalchemy import func
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


@router.get("/monitoring/observability")
async def get_observability_status(
    session: AsyncSession = Depends(get_db),
    admin: User = Depends(get_current_admin),
) -> dict[str, Any]:
    """
    Single admin surface for the observability stack (MD §8.3/§8.8): which
    collectors are wired, which are live, plus the latest drift + cost rollups.
    Honest signal — an enabled-but-uninstalled Langfuse is reported as such.
    """
    drift: dict[str, Any] = {}
    try:
        drift = await DriftService(session).drift_report()
    except Exception as exc:
        drift = {"error": f"Drift report unavailable: {exc}"}

    cost = {"total_usd": 0.0, "period_count": 0}
    try:
        row = (
            await session.exec(
                select(func.sum(CostLog.total_cost), func.count(CostLog.id))
            )
        ).one()
        cost = {"total_usd": float(row[0] or 0.0), "period_count": int(row[1] or 0)}
    except Exception as exc:
        logger.warning("cost_rollup_query_failed", error=str(exc))

    return {
        "stack": observability_stack_status(),
        "drift": drift,
        "cost": cost,
    }


@router.get("/monitoring/viewer-data")
async def get_observability_viewer_data(
    session: AsyncSession = Depends(get_db),
    admin: User = Depends(get_current_admin),
) -> dict[str, Any]:
    """JSON payload behind the admin observability dashboard (cost/usage/drift/evals)."""
    return await gather_viewer_data(session)


@router.get("/monitoring/viewer", response_class=HTMLResponse)
async def get_observability_viewer(
    session: AsyncSession = Depends(get_db),
    admin: User = Depends(get_current_admin),
) -> HTMLResponse:
    """
    Self-contained admin HTML dashboard: renders the observability stack status,
    cost rollups, usage telemetry, drift report and evaluation scorecard with
    zero external dependencies (inline CSS only — no CDN, no JS framework).
    """
    data = await gather_viewer_data(session)
    return HTMLResponse(content=render_viewer_html(data))


# --------------------------------------------------------------------------- #
# Lifecycle hook policies (block / redact / log at the tool gateway choke point)
# --------------------------------------------------------------------------- #

def _serialize_hook(policy) -> dict[str, Any]:
    return {
        "id": str(policy.id),
        "name": policy.name,
        "tool_name": policy.tool_name,
        "event": policy.event,
        "org_id": str(policy.org_id) if policy.org_id else None,
        "action": policy.action,
        "field": policy.field,
        "message": policy.message,
        "enabled": policy.enabled,
        "created_at": policy.created_at.isoformat() if policy.created_at else None,
        "updated_at": policy.updated_at.isoformat() if policy.updated_at else None,
    }


@router.get("/hooks")
async def list_hook_policies(
    session: AsyncSession = Depends(get_db),
    admin: User = Depends(get_current_admin),
) -> list[dict[str, Any]]:
    """List lifecycle hook policies (global + org-scoped)."""
    policies = await HookService(session).list_policies()
    return [_serialize_hook(p) for p in policies]


@router.post("/hooks", status_code=201)
async def create_hook_policy(
    body: HookPolicyCreate,
    session: AsyncSession = Depends(get_db),
    admin: User = Depends(get_current_admin),
) -> dict[str, Any]:
    """Create a hook policy and hot-reload the gateway registry."""
    policy = await HookService(session).create_policy(body)
    return _serialize_hook(policy)


@router.put("/hooks/{policy_id}")
async def update_hook_policy(
    policy_id: UUID,
    body: HookPolicyUpdate,
    session: AsyncSession = Depends(get_db),
    admin: User = Depends(get_current_admin),
) -> dict[str, Any]:
    """Update a hook policy and hot-reload the gateway registry."""
    from backend.app.core.exceptions import ResourceNotFoundError
    try:
        policy = await HookService(session).update_policy(policy_id, body)
    except ResourceNotFoundError:
        raise HTTPException(status_code=404, detail="Hook policy not found")
    return _serialize_hook(policy)


@router.delete("/hooks/{policy_id}", status_code=204)
async def delete_hook_policy(
    policy_id: UUID,
    session: AsyncSession = Depends(get_db),
    admin: User = Depends(get_current_admin),
) -> None:
    """Delete a hook policy and hot-reload the gateway registry."""
    from backend.app.core.exceptions import ResourceNotFoundError
    try:
        await HookService(session).delete_policy(policy_id)
    except ResourceNotFoundError:
        raise HTTPException(status_code=404, detail="Hook policy not found")


@router.post("/hooks/reload")
async def reload_hook_policies(
    session: AsyncSession = Depends(get_db),
    admin: User = Depends(get_current_admin),
) -> dict[str, Any]:
    """Re-snapshot the in-memory hook registry from the database (fail-open)."""
    count = await HookService(session).reload_registry()
    return {"reloaded": True, "policy_count": count}
