"""`error.type` on the chat spans is bounded and never carries user content.

The chat spans added around the persistence tail re-use ``trace_span``. Its
error path already records the exception class as ``error_type`` (underscore),
but the APM semantic convention reads ``error.type`` (dotted) — without it an
errored span carries no machine-readable error category. These tests inject a
fake tracer rather than requiring the OTel SDK, so they hold on a bare install.
"""
from __future__ import annotations

import sys
from contextlib import contextmanager
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from backend.app.services.observability import tracing as tracing_mod  # noqa: E402


class _Span:
    def __init__(self) -> None:
        self.attributes: dict[str, object] = {}
        self.exceptions: list[BaseException] = []

    def set_attribute(self, key: str, value: object) -> None:
        self.attributes[key] = value

    def record_exception(self, exc: BaseException) -> None:
        self.exceptions.append(exc)


class _Tracer:
    def __init__(self, span: _Span) -> None:
        self.span = span

    @contextmanager
    def start_as_current_span(self, name: str):
        yield self.span


class _LogRecorder:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict]] = []

    def info(self, event: str, **fields: object) -> None:
        self.events.append((event, fields))

    def error(self, event: str, **fields: object) -> None:
        self.events.append((event, fields))

    def debug(self, event: str, **fields: object) -> None:
        self.events.append((event, fields))


@pytest.fixture
def fake_tracer(monkeypatch: pytest.MonkeyPatch) -> tuple[_Span, _LogRecorder]:
    span = _Span()
    logs = _LogRecorder()
    monkeypatch.setattr(tracing_mod, "get_tracer", lambda name: _Tracer(span))
    monkeypatch.setattr(tracing_mod, "logger", logs)
    return span, logs


async def test_an_errored_chat_span_carries_a_bounded_error_type(fake_tracer):
    """The class name is a fixed identifier; the exception text is not copied."""
    span, logs = fake_tracer
    secret = "the user's unredacted prompt"

    with pytest.raises(ValueError):
        async with tracing_mod.trace_span(
            "chat.persist.usage_log", {"run_id": "r-1"}
        ):
            raise ValueError(secret)

    assert span.attributes["error.type"] == "ValueError"
    assert span.attributes["error_type"] == "ValueError"
    assert secret not in [str(value) for value in span.attributes.values()]
    assert span.exceptions and isinstance(span.exceptions[0], ValueError)

    error_events = [fields for event, fields in logs.events if event.startswith("span_error_")]
    assert error_events
    assert error_events[0]["error.type"] == "ValueError"


async def test_a_successful_chat_span_has_no_error_type(fake_tracer):
    """Absence of an error must not be reported as an error category."""
    span, _ = fake_tracer

    async with tracing_mod.trace_span("chat.post_run_frames", {"run_id": "r-1"}):
        pass

    assert "error.type" not in span.attributes
    assert span.attributes["status"] == "success"
