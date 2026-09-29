"""
Unit tests for PII/secret redaction utilities (log hygiene).
"""
import structlog
from backend.app.core import redaction
from backend.app.core.redaction import redact_event, redact_text


def test_redacts_email_phone_ssn():
    text = "Contact alice@example.com or 415-555-2671. SSN 123-45-6789."
    scrubbed = redact_text(text)
    assert "alice@example.com" not in scrubbed
    assert "415-555-2671" not in scrubbed
    assert "123-45-6789" not in scrubbed
    assert "[REDACTED]" in scrubbed


def test_redacts_credit_card():
    scrubbed = redact_text("Card 4111 1111 1111 1111 expires soon")
    assert "4111 1111 1111 1111" not in scrubbed


def test_redacts_private_key_blob():
    key = "-----BEGIN RSA PRIVATE KEY-----\nMIIEowIBAAKCAQEAu9um\n-----END RSA PRIVATE KEY-----"
    scrubbed = redact_text(f"leak: {key}")
    assert "BEGIN RSA PRIVATE KEY" not in scrubbed
    assert "MIIEowIBAAKCAQEAu9um" not in scrubbed


def test_redacts_bearer_and_aws_tokens():
    text = "Authorization Bearer sk-abcdefghijklmnop12345678 and AKIAIOSFODNN7EXAMPLE"
    scrubbed = redact_text(text)
    assert "sk-abcdefghijklmnop12345678" not in scrubbed
    assert "AKIAIOSFODNN7EXAMPLE" not in scrubbed


def test_plain_text_untouched():
    text = "What is the capital of France? Paris has 12 arrondissements in the core."
    assert redact_text(text) == text


def test_secret_typed_keys_replaced_wholesale():
    event = {
        "event": "request",
        "api_key": "real-value-123",
        "nested": {"password": "hunter2", "note": "call 415-555-2671"},
    }
    scrubbed = redact_event(None, "info", event)
    assert scrubbed["api_key"] == "[REDACTED]"
    assert scrubbed["nested"]["password"] == "[REDACTED]"
    assert "415-555-2671" not in scrubbed["nested"]["note"]


def test_processor_play_nicely_with_structlog_bind():
    logger = structlog.get_logger("test-redaction")
    event_dict = {"event": "hello", "email": "x@y.com"}
    out = redact_event(logger, "info", event_dict)
    assert out["email"] == "[REDACTED]"
    assert redaction.redact_text("plain") == "plain"
