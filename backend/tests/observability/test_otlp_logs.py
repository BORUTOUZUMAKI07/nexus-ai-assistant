"""
Unit tests for the Layer-1 OTLP log export (env-gated, fail-open).

The handler is attached to the ROOT stdlib logger, so every line structlog
emits also flows to the Layer-2 collector over OTLP/HTTP (the collector owns
the New Relic ingest key). Tests are hermetic: the OTLP HTTP exporter class is
replaced with an in-memory fake, so no network ever happens and the rest of
the SDK pipeline (handler → provider → batch processor) still runs for real.
"""
import importlib.util
import logging

import pytest
from backend.app.core.config import settings
from backend.app.services.observability import logs as otlp_logs

OTEL_INSTALLED = importlib.util.find_spec("opentelemetry") is not None


def _set(name: str, value) -> None:
    setattr(settings, name, value)


@pytest.fixture(autouse=True)
def _reset_otlp_state():
    """Each test starts and ends with a clean, unwired OTLP log export state."""
    otlp_logs.shutdown_otlp_log_handler()
    yield
    otlp_logs.shutdown_otlp_log_handler()


def _body_text(log_data) -> str:
    record = getattr(log_data, "log_record", log_data)
    body = getattr(record, "body", None)
    if body is None:
        return ""
    if hasattr(body, "string_value"):
        return body.string_value or ""
    return str(body)


def test_disabled_export_returns_none():
    original = settings.NEW_RELIC_ENABLED
    try:
        _set("NEW_RELIC_ENABLED", False)
        assert otlp_logs.setup_otlp_log_handler() is None
    finally:
        _set("NEW_RELIC_ENABLED", original)


def test_enabled_without_endpoint_returns_none():
    original_enabled = settings.NEW_RELIC_ENABLED
    original_endpoint = settings.NEW_RELIC_OTLP_ENDPOINT
    try:
        _set("NEW_RELIC_ENABLED", True)
        _set("NEW_RELIC_OTLP_ENDPOINT", "")
        assert otlp_logs.setup_otlp_log_handler() is None
    finally:
        _set("NEW_RELIC_ENABLED", original_enabled)
        _set("NEW_RELIC_OTLP_ENDPOINT", original_endpoint)


def test_endpoint_builds_v1_logs_path():
    original_endpoint = settings.NEW_RELIC_OTLP_ENDPOINT
    try:
        _set("NEW_RELIC_OTLP_ENDPOINT", "http://localhost:4318/")
        assert otlp_logs._otlp_logs_endpoint() == "http://localhost:4318/v1/logs"
    finally:
        _set("NEW_RELIC_OTLP_ENDPOINT", original_endpoint)


@pytest.mark.skipif(not OTEL_INSTALLED, reason="[observability] extras not installed")
def test_enabled_wires_handler_and_flushes_to_collector(monkeypatch):
    original_enabled = settings.NEW_RELIC_ENABLED
    original_endpoint = settings.NEW_RELIC_OTLP_ENDPOINT

    class FakeExporter:
        """In-memory stand-in for OTLPLogExporter — captures batched records."""

        def __init__(self):
            self.exported = []

        def export(self, log_data_list):
            from opentelemetry.sdk._logs.export import LogExportResult

            self.exported.extend(log_data_list)
            return LogExportResult.SUCCESS

        def shutdown(self):
            pass

    fake = FakeExporter()

    try:
        _set("NEW_RELIC_ENABLED", True)
        _set("NEW_RELIC_OTLP_ENDPOINT", "http://localhost:4318")
        monkeypatch.setattr(
            "opentelemetry.exporter.otlp.proto.http._log_exporter.OTLPLogExporter",
            lambda *a, **k: fake,
        )

        handler = otlp_logs.setup_otlp_log_handler(logging.DEBUG)
        assert handler is not None
        assert any(h is handler for h in logging.getLogger().handlers)

        # Levels are unrelated to the app's bootstrapped log level — pin the
        # tree so the record is guaranteed to reach the attached handler.
        logging.getLogger().setLevel(logging.DEBUG)
        logging.getLogger("test-otlp").setLevel(logging.DEBUG)
        logging.getLogger("test-otlp").info("otlp log export test signal")

        otlp_logs.shutdown_otlp_log_handler()
        assert all(h is not handler for h in logging.getLogger().handlers)

        # shutdown flushed the batch processor synchronously → the fake got it.
        assert len(fake.exported) >= 1
        assert any("otlp log export test signal" in _body_text(item) for item in fake.exported)
    finally:
        _set("NEW_RELIC_ENABLED", original_enabled)
        _set("NEW_RELIC_OTLP_ENDPOINT", original_endpoint)


@pytest.mark.skipif(not OTEL_INSTALLED, reason="[observability] extras not installed")
def test_shutdown_is_idempotent_and_reset_allows_rewire(monkeypatch):
    original_enabled = settings.NEW_RELIC_ENABLED
    original_endpoint = settings.NEW_RELIC_OTLP_ENDPOINT

    class FakeExporter:
        def __init__(self):
            self.exported = []

        def export(self, log_data_list):
            from opentelemetry.sdk._logs.export import LogExportResult

            self.exported.extend(log_data_list)
            return LogExportResult.SUCCESS

        def shutdown(self):
            pass

    try:
        _set("NEW_RELIC_ENABLED", True)
        _set("NEW_RELIC_OTLP_ENDPOINT", "http://localhost:4318")
        monkeypatch.setattr(
            "opentelemetry.exporter.otlp.proto.http._log_exporter.OTLPLogExporter",
            lambda *a, **k: FakeExporter(),
        )

        # Shutdown with nothing wired is a silent no-op.
        otlp_logs.shutdown_otlp_log_handler()
        otlp_logs.shutdown_otlp_log_handler()

        first = otlp_logs.setup_otlp_log_handler()
        # A second call returns the SAME handler (no double-attach).
        assert otlp_logs.setup_otlp_log_handler() is first

        otlp_logs.shutdown_otlp_log_handler()
        # After shutdown the wiring can be re-done.
        second = otlp_logs.setup_otlp_log_handler()
        assert second is not None and second is not first
        otlp_logs.shutdown_otlp_log_handler()
    finally:
        _set("NEW_RELIC_ENABLED", original_enabled)
        _set("NEW_RELIC_OTLP_ENDPOINT", original_endpoint)
