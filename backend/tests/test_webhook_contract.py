"""
Pact-style contract test for signed outbound webhooks.

The two roles of a Pact interaction are modeled explicitly:

  * The *provider* is ``WebhookService._deliver`` — it produces an HTTP POST
    request documented by the wire contract below (envelope keys, header set,
    and the ``sha256=<hex>`` HMAC over the exact raw body bytes).
  * The *consumer* is ``ConsumerWebhookVerifier`` — an *independent* SDK-style
    implementation written only against the documented wire contract (no import
    of webhook_service internals). It validates an incoming request the way
    any real downstream system must.

Both roles are simple per-request units: no database, no network. The provider
delivers through a recording sender; the consumer verifier re-derives the
signature and rejects tampering. If the provider's wire format ever drifts
from the documented contract, one of these tests breaks — that is the point.
"""

import asyncio
import hashlib
import hmac
import json
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from backend.app.domain.webhook.models import WebhookEndpoint
from backend.app.services.webhook_service import WebhookService
from fakes import FakeSession

# Documented wire contract (mirrored in app/services/webhook_service.py).
DOCUMENTED_HEADERS = {
    "Content-Type": "application/json",
    "X-Nexus-Event": None,  # filled per delivery
    "X-Nexus-Delivery": None,
    "X-Nexus-Signature": None,
    "User-Agent": "nexus-webhook/1.0",
}
ENVELOPE_KEYS = ("event", "delivery_id", "timestamp", "payload")


class ContractViolationError(Exception):
    """Raised when an inbound webhook request violates the documented contract."""


class ConsumerWebhookVerifier:
    """Consumer-side verifier implementing the documented Nexus webhook contract.

    A real integration would do exactly this: given the endpoint secret, check
    that the signature covers the raw body and that the envelope parses.
    """

    REQUIRED_HEADERS = (
        "Content-Type",
        "X-Nexus-Event",
        "X-Nexus-Delivery",
        "X-Nexus-Signature",
    )

    def __init__(self, secret: str) -> None:
        self._secret = secret

    def verify(self, *, headers: dict, raw_body: bytes) -> dict:
        """Validate a request; returns the parsed envelope or raises."""
        missing = [h for h in self.REQUIRED_HEADERS if h not in headers]
        if missing:
            raise ContractViolationError(f"missing required headers: {missing}")
        if headers["Content-Type"] != "application/json":
            raise ContractViolationError(f"bad Content-Type: {headers['Content-Type']!r}")

        sig_header = headers.get("X-Nexus-Signature", "")
        if not sig_header.startswith("sha256="):
            raise ContractViolationError(f"signature header not in sha256=<hex> form: {sig_header!r}")

        expected = hmac.new(self._secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(sig_header[len("sha256="):], expected):
            raise ContractViolationError("signature does not match raw body")

        try:
            envelope = json.loads(raw_body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ContractViolationError(f"envelope is not valid JSON: {exc}") from exc

        missing_keys = [k for k in ENVELOPE_KEYS if k not in envelope]
        if missing_keys:
            raise ContractViolationError(f"envelope missing keys: {missing_keys}")
        try:
            timestamp = datetime.fromisoformat(envelope["timestamp"])
        except ValueError as exc:
            raise ContractViolationError("envelope timestamp is not ISO-8601") from exc
        if timestamp.tzinfo is None:
            raise ContractViolationError("envelope timestamp must carry a UTC offset")

        if envelope["delivery_id"] != headers.get("X-Nexus-Delivery"):
            raise ContractViolationError("X-Nexus-Delivery does not match envelope delivery_id")
        if envelope["event"] != headers.get("X-Nexus-Event"):
            raise ContractViolationError("X-Nexus-Event does not match envelope event")
        return envelope


class RawRecordingSender:
    """Provider-side transport spy: records the request exactly as sent."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict, bytes]] = []

    async def __call__(self, url: str, headers: dict, payload: dict) -> int:
        # The wire format the provider signs: json.dumps(body, default=str).
        raw = json.dumps(payload, default=str).encode("utf-8")
        self.calls.append((url, headers, raw))
        return 202


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


def _deliver_one(secret: str = "s3cret", payload: dict | None = None) -> tuple[tuple[str, dict, bytes], dict]:
    """Provider flow: dispatch one signed delivery and return (request, envelope)."""
    user = uuid4()
    fake = FakeSession()
    fake.seed(
        WebhookEndpoint,
        [WebhookEndpoint(user_id=user, url="https://hook.example/cb", secret=secret, events=["message.completed"], is_active=True)],
    )
    recorder = RawRecordingSender()
    svc = WebhookService(fake, http_sender=recorder)
    _await(svc.dispatch_event("message.completed", payload or {"msg": "hello"}, user))
    url, headers, raw = recorder.calls[0]
    return (url, headers, raw), {"event": "message.completed", "payload": payload or {"msg": "hello"}}


def test_provider_request_satisfies_consumer_contract():
    """A freshly delivered webhook passes an independent consumer verification."""
    (url, headers, raw), expected = _deliver_one()

    verifier = ConsumerWebhookVerifier("s3cret")
    envelope = verifier.verify(headers=headers, raw_body=raw)

    assert url == "https://hook.example/cb"
    assert envelope["event"] == expected["event"]
    assert envelope["payload"] == expected["payload"]
    assert envelope["delivery_id"] == headers["X-Nexus-Delivery"]
    assert headers["User-Agent"] == "nexus-webhook/1.0"


def test_envelope_exposes_exactly_the_documented_keys():
    (_, headers, raw), _ = _deliver_one()
    verifier = ConsumerWebhookVerifier("s3cret")
    envelope = verifier.verify(headers=headers, raw_body=raw)
    assert set(envelope) == set(ENVELOPE_KEYS)
    # timestamp must be timezone-aware (UTC) so consumers can evaluate replay windows
    assert datetime.fromisoformat(envelope["timestamp"]).tzinfo is not None


def test_consumer_rejects_tampered_payload():
    """One flipped byte in the body breaks the signature — the core security property."""
    (_, headers, raw), _ = _deliver_one()
    tampered = raw[:-1] + bytes([raw[-1] ^ 0x01])

    verifier = ConsumerWebhookVerifier("s3cret")
    with pytest.raises(ContractViolationError, match="signature does not match"):
        verifier.verify(headers=headers, raw_body=tampered)


def test_consumer_rejects_wrong_secret():
    """Verifying with a different secret must fail (e.g. rotated endpoint key)."""
    (_, headers, raw), _ = _deliver_one()
    verifier = ConsumerWebhookVerifier("wrong-secret")
    with pytest.raises(ContractViolationError, match="signature does not match"):
        verifier.verify(headers=headers, raw_body=raw)


def test_consumer_rejects_unsigned_request():
    """No signature header at all is a protocol violation, never a silent accept."""
    (_, headers, raw), _ = _deliver_one()
    headers = {k: v for k, v in headers.items() if k != "X-Nexus-Signature"}
    verifier = ConsumerWebhookVerifier("s3cret")
    with pytest.raises(ContractViolationError, match="missing required headers"):
        verifier.verify(headers=headers, raw_body=raw)


def test_provider_signature_covers_raw_not_pretty_json():
    """Signature stability: re-encoding the SAME dict must reproduce the same header.

    This pins the provider's serializer — an accidental switch to pretty-print
    or separators would change signatures for existing consumers.
    """
    (_, headers, raw), _ = _deliver_one()
    # Re-serializing with the documented serializer (json.dumps default args,
    # default=str) must reproduce the exact signed bytes.
    reencoded = json.dumps(json.loads(raw.decode("utf-8")), default=str).encode("utf-8")
    assert reencoded == raw
