"""
Unit tests for signed outbound webhooks: endpoint management, HMAC signatures,
owner-scoped dispatch, redelivery, and bounded retries.
"""
import asyncio
import hashlib
import hmac
import json
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from backend.app.core.config import settings
from backend.app.domain.webhook.models import WebhookDelivery, WebhookEndpoint
from backend.app.services.webhook_service import (
    WebhookService,
    retry_failed_deliveries,
)
from fakes import FakeSession


def _await(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


def _endpoint(user_id, url, events=("message.completed",), active=True, secret="s3cret"):
    return WebhookEndpoint(
        user_id=user_id,
        url=url,
        secret=secret,
        events=list(events),
        is_active=active,
    )


class Recorder:
    def __init__(self):
        self.calls = []

    async def __call__(self, url, headers, payload):
        self.calls.append((url, headers, payload))
        return 200


def test_register_validates_scheme():
    fake = FakeSession()
    svc = WebhookService(fake)
    with pytest.raises(ValueError):
        _await(svc.register(uuid4(), "ftp://example.com/hook", ["message.completed"]))

    user = uuid4()
    endpoint = _await(svc.register(user, "https://example.com/hook", []))
    assert endpoint.events == ["message.completed"]  # default subscription
    assert endpoint.secret
    assert endpoint in fake.rows[WebhookEndpoint]


def test_dispatch_is_owner_and_flag_scoped():
    user_a, user_b = uuid4(), uuid4()
    fake = FakeSession()
    active_a = _endpoint(user_a, "https://hook.example/a")
    inactive_a = _endpoint(user_a, "https://hook.example/a2", active=False)
    other_b = _endpoint(user_b, "https://hook.example/b")  # events match but different owner
    fake.seed(WebhookEndpoint, [active_a, inactive_a, other_b])

    recorder = Recorder()
    svc = WebhookService(fake, http_sender=recorder)
    count = _await(svc.dispatch_event("message.completed", {"msg": "hi"}, user_a))

    assert count == 1  # only the active + matching endpoint of user A
    assert len(recorder.calls) == 1


def test_delivery_is_hmac_signed():
    user = uuid4()
    secret = "per-endpoint-secret"
    fake = FakeSession()
    fake.seed(WebhookEndpoint, [_endpoint(user, "https://hook.example/a", secret=secret)])
    recorder = Recorder()
    svc = WebhookService(fake, http_sender=recorder)

    _await(svc.dispatch_event("message.completed", {"msg": "hi"}, user))

    url, headers, payload = recorder.calls[0]
    assert url == "https://hook.example/a"
    assert headers["X-Nexus-Event"] == "message.completed"
    expected_raw = json.dumps(payload, default=str).encode("utf-8")
    expected_sig = hmac.new(secret.encode(), expected_raw, hashlib.sha256).hexdigest()
    assert headers["X-Nexus-Signature"] == f"sha256={expected_sig}"

    # Delivery attempt recorded on the user's endpoint.
    deliveries = _await(svc.deliveries(fake.rows[WebhookEndpoint][0].id))
    assert len(deliveries) == 1
    assert deliveries[0].status == "delivered"
    assert deliveries[0].http_status == 200
    assert deliveries[0].attempts == 1


def test_delivery_records_failure():
    user = uuid4()
    fake = FakeSession()
    fake.seed(WebhookEndpoint, [_endpoint(user, "https://hook.example/fail")])

    async def failing_sender(url, headers, payload):
        return 500

    svc = WebhookService(fake, http_sender=failing_sender)
    _await(svc.dispatch_event("message.completed", {"msg": "x"}, user))
    deliveries = _await(svc.deliveries(fake.rows[WebhookEndpoint][0].id))
    assert deliveries[0].status == "failed"
    assert deliveries[0].error_message


def test_set_active_and_delete():
    user = uuid4()
    fake = FakeSession()
    ep = _endpoint(user, "https://hook.example/x")
    fake.seed(WebhookEndpoint, [ep])

    svc = WebhookService(fake)
    toggled = _await(svc.set_active(user, ep.id, False))
    assert toggled.is_active is False

    assert _await(svc.delete(user, ep.id)) is True
    assert fake.rows[WebhookEndpoint] == []
    assert _await(svc.delete(uuid4(), ep.id)) is False  # cross-owner


def test_redeliver_owner_only():
    user_a, user_b = uuid4(), uuid4()
    fake = FakeSession()
    ep = _endpoint(user_a, "https://hook.example/x")
    fake.seed(WebhookEndpoint, [ep])
    delivery = WebhookDelivery(
        endpoint_id=ep.id,
        event="message.completed",
        payload_json={"msg": "hi"},
        status="failed",
        attempts=2,
        error_message="Endpoint returned HTTP 500",
    )
    fake.seed(WebhookDelivery, [delivery])

    recorder = Recorder()
    svc = WebhookService(fake, http_sender=recorder)

    # Cross-user: joined ownership check must reject.
    assert _await(svc.redeliver(user_b, delivery.id)) is None

    fixed = _await(svc.redeliver(user_a, delivery.id))
    assert fixed is not None
    assert fixed.status == "delivered"
    assert fixed.attempts == 1  # reset then re-attempted once
    assert len(recorder.calls) == 1


def test_retry_failed_deliveries_is_bounded(monkeypatch):
    user = uuid4()
    fake = FakeSession()
    ep = _endpoint(user, "https://hook.example/y")
    fake.seed(WebhookEndpoint, [ep])
    fake.seed(
        WebhookDelivery,
        [
            WebhookDelivery(
                endpoint_id=ep.id, event="message.completed", payload_json={},
                status="failed", attempts=1,
            ),
            WebhookDelivery(
                endpoint_id=ep.id, event="message.completed", payload_json={},
                status="failed", attempts=settings.WEBHOOK_MAX_ATTEMPTS,  # at the cap
            ),
        ],
    )

    recorder = Recorder()
    monkeypatch.setattr(WebhookService, "_default_sender", recorder)
    retried = _await(retry_failed_deliveries(fake))
    assert retried == 1  # only the below-cap delivery
    deliveries = fake.rows[WebhookDelivery]
    assert any(d.attempts == 2 for d in deliveries)  # retried +1
    assert any(d.attempts == settings.WEBHOOK_MAX_ATTEMPTS for d in deliveries)


def test_manual_redeliver_unknown_delivery():
    user = uuid4()
    fake = FakeSession()
    ep = _endpoint(user, "https://hook.example/z")
    fake.seed(WebhookEndpoint, [ep])
    svc = WebhookService(fake, http_sender=Recorder())
    assert _await(svc.redeliver(user, uuid4())) is None