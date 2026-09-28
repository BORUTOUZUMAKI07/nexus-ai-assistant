"""
Outbound webhook API — signed event delivery to consumer URLs.
"""
from uuid import UUID

import structlog
from backend.app.api.deps import get_current_user, get_webhook_service
from backend.app.domain.user.models import User
from backend.app.services.webhook_service import WebhookService
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/webhooks", tags=["webhooks"])


class WebhookCreate(BaseModel):
    url: str = Field(min_length=5)
    events: list[str] = Field(default_factory=lambda: ["message.completed"])
    secret: str | None = None


class WebhookToggle(BaseModel):
    is_active: bool


@router.post("", status_code=status.HTTP_201_CREATED)
async def register_webhook(
    body: WebhookCreate,
    current_user: User = Depends(get_current_user),
    webhook_svc: WebhookService = Depends(get_webhook_service),
):
    """Register an endpoint. Returns the (generated if absent) HMAC signing secret once."""
    try:
        endpoint = await webhook_svc.register(
            user_id=current_user.id, url=body.url, events=body.events, secret=body.secret
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {
        "id": str(endpoint.id),
        "url": endpoint.url,
        "events": endpoint.events,
        "is_active": endpoint.is_active,
        "secret": endpoint.secret,
        "created_at": endpoint.created_at.isoformat() if endpoint.created_at else None,
    }


@router.get("")
async def list_webhooks(
    current_user: User = Depends(get_current_user),
    webhook_svc: WebhookService = Depends(get_webhook_service),
):
    endpoints = await webhook_svc.list_for_user(current_user.id)
    return [
        {
            "id": str(e.id),
            "url": e.url,
            "events": e.events,
            "is_active": e.is_active,
            "has_secret": bool(e.secret),
            "created_at": e.created_at.isoformat() if e.created_at else None,
        }
        for e in endpoints
    ]


@router.get("/{endpoint_id}/deliveries")
async def list_deliveries(
    endpoint_id: UUID,
    limit: int = 20,
    current_user: User = Depends(get_current_user),
    webhook_svc: WebhookService = Depends(get_webhook_service),
):
    if not await webhook_svc._owned_endpoint(current_user.id, endpoint_id):
        raise HTTPException(status_code=404, detail="Webhook endpoint not found")
    deliveries = await webhook_svc.deliveries(endpoint_id, limit=limit)
    return [
        {
            "id": str(d.id),
            "event": d.event,
            "status": d.status,
            "http_status": d.http_status,
            "attempts": d.attempts,
            "error": d.error_message,
            "created_at": d.created_at.isoformat() if d.created_at else None,
            "delivered_at": d.delivered_at.isoformat() if d.delivered_at else None,
        }
        for d in deliveries
    ]


@router.post("/{endpoint_id}/redeliver")
async def redeliver(
    endpoint_id: UUID,
    delivery_id: UUID,
    current_user: User = Depends(get_current_user),
    webhook_svc: WebhookService = Depends(get_webhook_service),
):
    """Re-attempt a failed delivery endpoint (owner-scoped)."""
    deliveries = await webhook_svc.deliveries(endpoint_id, limit=100)
    if not any(str(d.id) == str(delivery_id) for d in deliveries):
        raise HTTPException(status_code=404, detail="Delivery not found")
    result = await webhook_svc.redeliver(current_user.id, delivery_id)
    if not result:
        raise HTTPException(status_code=404, detail="Delivery not found")
    return {"id": str(result.id), "status": result.status, "http_status": result.http_status}


@router.patch("/{endpoint_id}")
async def toggle_webhook(
    endpoint_id: UUID,
    body: WebhookToggle,
    current_user: User = Depends(get_current_user),
    webhook_svc: WebhookService = Depends(get_webhook_service),
):
    endpoint = await webhook_svc.set_active(current_user.id, endpoint_id, body.is_active)
    if not endpoint:
        raise HTTPException(status_code=404, detail="Webhook endpoint not found")
    return {"id": str(endpoint.id), "is_active": endpoint.is_active}


@router.delete("/{endpoint_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_webhook(
    endpoint_id: UUID,
    current_user: User = Depends(get_current_user),
    webhook_svc: WebhookService = Depends(get_webhook_service),
):
    if not await webhook_svc.delete(current_user.id, endpoint_id):
        raise HTTPException(status_code=404, detail="Webhook endpoint not found")
