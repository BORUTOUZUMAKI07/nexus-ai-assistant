"""
Signed outbound webhook service.

Endpoints register a URL + event subscriptions + a per-endpoint HMAC secret.
Every delivery is recorded in ``webhook_deliveries`` and signed with
``X-Nexus-Signature: sha256=<hex hmac>`` over the raw JSON body, so consumers
can verify authenticity. Delivery is best-effort with bounded retries
(``WEBHOOK_MAX_ATTEMPTS``); failures are surfaced via endpoints/redeliver.
"""
import hashlib
import hmac
import secrets
from datetime import UTC, datetime
from urllib.parse import urlparse
from uuid import UUID

import httpx
import structlog
from backend.app.core.config import settings
from backend.app.domain.webhook.models import WebhookDelivery, WebhookEndpoint
from sqlmodel import delete, select
from sqlmodel.ext.asyncio.session import AsyncSession

logger = structlog.get_logger(__name__)


class WebhookService:
    def __init__(self, session: AsyncSession, http_sender=None) -> None:
        self.session = session
        # Injectable transport for tests (defaults to real httpx POST).
        self.http_sender = http_sender or self._default_sender

    # ── Endpoint management ───────────────────────────────────────────────────
    async def register(
        self,
        user_id: UUID,
        url: str,
        events: list[str],
        secret: str | None = None,
    ) -> WebhookEndpoint:
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            raise ValueError("Webhook URL must be a valid http(s) URL.")
        events = [e for e in (events or []) if e]
        if not events:
            events = ["message.completed"]
        endpoint = WebhookEndpoint(
            user_id=user_id,
            url=url,
            secret=secret or secrets.token_urlsafe(32),
            events=events,
        )
        self.session.add(endpoint)
        await self.session.commit()
        await self.session.refresh(endpoint)
        logger.info("webhook_registered", user_id=str(user_id), endpoint_id=str(endpoint.id))
        return endpoint

    async def list_for_user(self, user_id: UUID) -> list[WebhookEndpoint]:
        res = await self.session.exec(
            select(WebhookEndpoint).where(WebhookEndpoint.user_id == user_id)
        )
        return list(res.all())

    async def _owned_endpoint(self, user_id: UUID, endpoint_id: UUID) -> WebhookEndpoint | None:
        res = await self.session.exec(
            select(WebhookEndpoint).where(
                WebhookEndpoint.id == endpoint_id, WebhookEndpoint.user_id == user_id
            )
        )
        return res.first()

    async def delete(self, user_id: UUID, endpoint_id: UUID) -> bool:
        endpoint = await self._owned_endpoint(user_id, endpoint_id)
        if not endpoint:
            return False
        await self.session.exec(delete(WebhookDelivery).where(WebhookDelivery.endpoint_id == endpoint_id))
        await self.session.delete(endpoint)
        await self.session.commit()
        return True

    async def set_active(self, user_id: UUID, endpoint_id: UUID, is_active: bool) -> WebhookEndpoint | None:
        endpoint = await self._owned_endpoint(user_id, endpoint_id)
        if not endpoint:
            return None
        endpoint.is_active = is_active
        self.session.add(endpoint)
        await self.session.commit()
        await self.session.refresh(endpoint)
        return endpoint

    async def deliveries(self, endpoint_id: UUID, limit: int = 20) -> list[WebhookDelivery]:
        res = await self.session.exec(
            select(WebhookDelivery)
            .where(WebhookDelivery.endpoint_id == endpoint_id)
            .order_by(WebhookDelivery.created_at.desc())
            .limit(limit)
        )
        return list(res.all())

    # ── Delivery ──────────────────────────────────────────────────────────────
    async def dispatch_event(self, event: str, payload: dict, user_id: UUID) -> int:
        """Deliver ``event`` to every active matching endpoint. Returns delivery count."""
        endpoints = (await self.session.exec(
            select(WebhookEndpoint).where(
                WebhookEndpoint.user_id == user_id,
                WebhookEndpoint.is_active == True,  # noqa: E712
            )
        )).all()
        count = 0
        for endpoint in endpoints:
            if event in (endpoint.events or []) or "*" in (endpoint.events or []):
                await self._dispatch_to(endpoint, event, payload)
                count += 1
        return count

    async def redeliver(self, user_id: UUID, delivery_id: UUID) -> WebhookDelivery | None:
        res = await self.session.exec(
            select(WebhookDelivery).join(WebhookEndpoint, WebhookEndpoint.id == WebhookDelivery.endpoint_id)
            .where(
                WebhookDelivery.id == delivery_id,
                WebhookEndpoint.user_id == user_id,
            )
        )
        delivery = res.first()
        if not delivery:
            return None
        endpoint = await self._owned_endpoint(user_id, delivery.endpoint_id)
        if not endpoint:
            return None
        delivery.attempts = 0
        delivery.status = "pending"
        self.session.add(delivery)
        await self.session.commit()
        await self._deliver(endpoint, delivery)
        return delivery

    async def _dispatch_to(self, endpoint: WebhookEndpoint, event: str, payload: dict) -> None:
        delivery = WebhookDelivery(
            endpoint_id=endpoint.id,
            event=event,
            payload_json=payload,
        )
        self.session.add(delivery)
        await self.session.commit()
        await self.session.refresh(delivery)
        await self._deliver(endpoint, delivery)

    async def _deliver(self, endpoint: WebhookEndpoint, delivery: WebhookDelivery) -> None:
        body = {
            "event": delivery.event,
            "delivery_id": str(delivery.id),
            "timestamp": datetime.now(UTC).isoformat(),
            "payload": delivery.payload_json or {},
        }
        raw = __import__("json").dumps(body, default=str).encode("utf-8")
        signature = hmac.new(endpoint.secret.encode(), raw, hashlib.sha256).hexdigest()

        headers = {
            "Content-Type": "application/json",
            "X-Nexus-Event": delivery.event,
            "X-Nexus-Delivery": str(delivery.id),
            "X-Nexus-Signature": f"sha256={signature}",
            "User-Agent": "nexus-webhook/1.0",
        }
        try:
            status_code = await self.http_sender(endpoint.url, headers, body)
            delivery.status = "delivered" if 200 <= status_code < 300 else "failed"
            delivery.http_status = status_code
            delivery.delivered_at = datetime.now(UTC).replace(tzinfo=None)
            if not (200 <= status_code < 300):
                delivery.error_message = f"Endpoint returned HTTP {status_code}"
        except Exception as exc:
            delivery.status = "failed"
            delivery.error_message = str(exc)[:500]
            logger.warning("webhook_delivery_failed", endpoint_id=str(endpoint.id), error=str(exc))
        delivery.attempts = (delivery.attempts or 0) + 1
        self.session.add(delivery)
        await self.session.commit()

    async def _default_sender(self, url: str, headers: dict, payload: dict) -> int:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(url, json=payload, headers=headers)
            return resp.status_code


async def retry_failed_deliveries(
    session: AsyncSession, max_attempts: int | None = None
) -> int:
    """Re-attempt failed deliveries up to WEBHOOK_MAX_ATTEMPTS. Returns retried count."""
    limit = settings.WEBHOOK_MAX_ATTEMPTS
    service = WebhookService(session)
    res = await session.exec(
        select(WebhookDelivery).where(
            WebhookDelivery.status == "failed",
            WebhookDelivery.attempts < limit,
        )
    )
    retried = 0
    for delivery in res.all():
        endpoint = await session.get(WebhookEndpoint, delivery.endpoint_id)
        if endpoint and endpoint.is_active:
            await service._deliver(endpoint, delivery)
            retried += 1
    return retried
