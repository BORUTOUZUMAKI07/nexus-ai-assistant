import pytest

from backend.app.api.v1._replay_sanitizer import _sanitize_replayed_message


def test_sanitizer_redacts_pii():
    msg = {"role": "user", "content": "Contact me at test@example.com"}
    out = _sanitize_replayed_message(msg)
    assert "[EMAIL_REDACTED]" in out["content"]


def test_sanitizer_sanitizes_multimodal_parts():
    msg = {"role": "user", "content": [
        {"type": "text", "text": "test@example.com"},
        {"type": "image", "image_url": {"url": "x"}},
    ]}
    out = _sanitize_replayed_message(msg)
    assert out["content"][0]["text"] == "[EMAIL_REDACTED]"
    assert out["content"][1]["type"] == "image"
