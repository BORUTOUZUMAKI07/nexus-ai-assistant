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
    # pytest-asyncio tears the current loop down after each async test, so once
    # one has run get_event_loop() raises here. Re-establish a loop instead of
    # depending on collection order.
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    return loop.run_until_complete(coro)


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


# ── P0 #8 — SSRF guard on outbound webhook targets ────────────────────────────


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/hook",
        "http://localhost/hook",
        "http://169.254.169.254/latest/meta-data/",  # cloud metadata
        "http://10.0.0.5/hook",
        "http://192.168.1.1/hook",
        "http://[::1]/hook",
        "http://metadata.google.internal/hook",
    ],
)
def test_register_rejects_non_public_webhook_targets(url):
    fake = FakeSession()
    svc = WebhookService(fake)
    with pytest.raises(ValueError):
        _await(svc.register(uuid4(), url, ["message.completed"]))
    assert fake.rows.get(WebhookEndpoint, []) == []


def test_register_rejects_embedded_credentials():
    fake = FakeSession()
    svc = WebhookService(fake)
    with pytest.raises(ValueError):
        _await(svc.register(uuid4(), "https://user:pass@example.com/hook", []))


def test_delivery_revalidates_the_target_before_sending():
    """A persisted endpoint whose URL is private must not be fetched, even if it
    was somehow stored (DNS rebinding / pre-guard rows)."""
    user = uuid4()
    fake = FakeSession()
    ep = _endpoint(user, "http://169.254.169.254/meta")
    fake.seed(WebhookEndpoint, [ep])

    recorder = Recorder()
    svc = WebhookService(fake, http_sender=recorder)
    _await(svc.dispatch_event("message.completed", {"msg": "x"}, user))

    assert recorder.calls == []  # never left the process
    deliveries = _await(svc.deliveries(ep.id))
    assert len(deliveries) == 1
    assert deliveries[0].status == "failed"
    assert deliveries[0].error_message


# ── P0 #11 — sign and send the exact same bytes ───────────────────────────────


def test_default_sender_posts_the_bytes_it_signed(monkeypatch):
    import backend.app.services.webhook_service as ws_mod

    captured = {}

    class _Resp:
        status_code = 200

    class _Client:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def post(self, url, content=None, headers=None, **kwargs):
            captured["url"] = url
            captured["content"] = content
            captured["headers"] = headers
            captured["kwargs"] = kwargs
            return _Resp()

    monkeypatch.setattr(ws_mod.httpx, "AsyncClient", _Client)

    svc = WebhookService(FakeSession())
    payload = {"event": "message.completed", "payload": {"msg": "hi"}}
    status = _await(svc._default_sender("https://hook.example/a", {"X": "y"}, payload))

    assert status == 200
    # The body on the wire is exactly the bytes the signature was computed over.
    assert captured["content"] == ws_mod._encode_webhook_body(payload)
    assert "json" not in captured["kwargs"]


# ── #54 — retry limit is configurable ─────────────────────────────────────────


def test_retry_failed_deliveries_honours_an_explicit_max_attempts(monkeypatch):
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
                status="failed", attempts=3,
            ),
        ],
    )

    recorder = Recorder()
    monkeypatch.setattr(WebhookService, "_default_sender", recorder)
    retried = _await(retry_failed_deliveries(fake, max_attempts=2))
    assert retried == 1  # only attempts=1 is below the override of 2
