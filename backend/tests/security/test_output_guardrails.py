"""One output guardrail, applied everywhere model text leaves the backend.

The synthesizer already redacted PII before streaming and persisting, but the
alternate ``/messages`` endpoints did not: their sync response, their SSE token
frames, their persisted row and their cache/webhook payloads all carried raw
model text. This file pins the shared primitive both paths must use, including
the incremental variant needed when tokens arrive one at a time.
"""
from __future__ import annotations

import pytest
from backend.app.services.evaluation.guardrail_service import (
    StreamingOutputSanitizer,
    guardrail_service,
)


def test_sanitize_output_redacts_pii_and_reports_counts():
    result = guardrail_service.sanitize_output(
        "Email alice@example.com or call 415-555-2671."
    )

    assert "alice@example.com" not in result.text
    assert "415-555-2671" not in result.text
    assert result.pii_counts == {"email": 1, "phone_us": 1}


def test_sanitize_output_fails_open(monkeypatch):
    def _boom(*_args, **_kwargs):
        raise RuntimeError("guardrail service unavailable")

    monkeypatch.setattr(guardrail_service, "redact_pii", _boom)

    result = guardrail_service.sanitize_output("An answer that must still ship.")

    assert result.text == "An answer that must still ship."
    assert result.pii_counts == {}


def test_incremental_sanitizer_matches_whole_text_redaction():
    full = (
        "Contact alice@example.com or call 415-555-2671. "
        "SSN 123-45-6789. Card 4111 1111 1111 1111. Done."
    )
    chunks = [
        "Contact alice@",
        "example.com or call 415-",
        "555-2671. SSN 123-45",
        "-6789. Card 4111 1111 ",
        "1111 1111. Done.",
    ]
    sanitizer = StreamingOutputSanitizer()

    pieces = [sanitizer.feed(chunk) for chunk in chunks]
    pieces.append(sanitizer.finish())
    emitted = "".join(pieces)

    assert emitted == guardrail_service.sanitize_output(full).text
    assert "alice@example.com" not in emitted
    assert "415-555-2671" not in emitted
    assert "123-45-6789" not in emitted
    assert "4111 1111 1111 1111" not in emitted
    # A frame must never contain a fragment of a PII value that is completed
    # by a later token.
    assert not any("alice@" in piece for piece in pieces)


def test_incremental_sanitizer_does_not_buffer_the_whole_answer():
    sanitizer = StreamingOutputSanitizer()

    first = sanitizer.feed(
        "This is a long opening paragraph with many ordinary words. "
    )
    rest = sanitizer.feed("And the conclusion.\n") + sanitizer.finish()

    assert first.strip(), "tokens must flow before the stream ends"
    assert first + rest == (
        "This is a long opening paragraph with many ordinary words. "
        "And the conclusion.\n"
    )


def test_incremental_sanitizer_fails_open(monkeypatch):
    def _boom(*_args, **_kwargs):
        raise RuntimeError("guardrail service unavailable")

    monkeypatch.setattr(guardrail_service, "redact_pii", _boom)
    sanitizer = StreamingOutputSanitizer()

    emitted = sanitizer.feed("An answer that still ships.") + sanitizer.finish()

    assert emitted == "An answer that still ships."
