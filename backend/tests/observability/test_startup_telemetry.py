"""Startup sends a real probe signal and checks that the SDK accepted it."""

from contextlib import asynccontextmanager
from types import SimpleNamespace

import backend.app.services.observability.startup_verification as probe
import pytest


class FakeSettings:
    NEW_RELIC_ENABLED = True
    LANGSMITH_TRACING = True
    LANGSMITH_API_KEY = "secret-ls-value"
    LANGSMITH_ENDPOINT = "https://aws.api.smith.langchain.com"
    LANGSMITH_PROJECT = "nexus-ai-assistant"
    STARTUP_TELEMETRY_TIMEOUT_SECONDS = 2.0


@pytest.fixture(autouse=True)
def _fake_settings(monkeypatch):
    monkeypatch.setattr(probe, "settings", FakeSettings())


@pytest.mark.asyncio
async def test_new_relic_span_is_emitted_and_flushed(monkeypatch):
    emitted = []

    @asynccontextmanager
    async def fake_trace_span(name, attributes=None):
        emitted.append(name)
        yield {}

    monkeypatch.setattr(probe, "trace_span", fake_trace_span)
    monkeypatch.setattr(probe, "force_flush_traces", lambda timeout_ms: True)

    result = await probe._verify_new_relic()

    assert emitted == ["app_startup"]
    assert result == {"status": "ok", "span": "app_startup", "detail": "span_flushed"}


@pytest.mark.asyncio
async def test_new_relic_flush_failure_is_degraded(monkeypatch):
    @asynccontextmanager
    async def fake_trace_span(name, attributes=None):
        yield {}

    monkeypatch.setattr(probe, "trace_span", fake_trace_span)
    monkeypatch.setattr(probe, "force_flush_traces", lambda timeout_ms: False)

    assert await probe._verify_new_relic() == {"status": "degraded", "detail": "flush_failed"}


@pytest.mark.asyncio
async def test_disabled_telemetry_is_not_configured(monkeypatch):
    monkeypatch.setattr(probe.settings, "NEW_RELIC_ENABLED", False)
    monkeypatch.setattr(probe.settings, "LANGSMITH_TRACING", False)

    assert await probe._verify_new_relic() == {"status": "not_configured"}
    assert await probe._verify_langsmith() == {"status": "not_configured"}


def test_langsmith_run_is_written_and_read_back(monkeypatch):
    created = {}

    class FakeClient:
        def __init__(self, **kwargs):
            created.update(kwargs)

        def create_run(self, **kwargs):
            created.update(kwargs)

        def read_run(self, run_id):
            assert run_id == created["id"]
            return {"id": run_id}

    monkeypatch.setattr(probe.langsmith, "Client", FakeClient)

    assert probe._langsmith_roundtrip() is True
    assert created["project_name"] == "nexus-ai-assistant"
    assert created["auto_batch_tracing"] is False


@pytest.mark.asyncio
async def test_langsmith_missing_run_is_degraded(monkeypatch):
    monkeypatch.setattr(probe, "_langsmith_roundtrip", lambda: False)

    assert await probe._verify_langsmith() == {"status": "degraded", "detail": "roundtrip_failed"}


@pytest.mark.asyncio
async def test_verify_updates_public_status(monkeypatch):
    async def fake_new_relic():
        return {"status": "ok"}

    async def fake_langsmith():
        return {"status": "degraded", "detail": "timeout"}

    monkeypatch.setattr(probe, "_verify_new_relic", fake_new_relic)
    monkeypatch.setattr(probe, "_verify_langsmith", fake_langsmith)

    await probe.verify_startup_telemetry()

    assert probe.get_startup_telemetry_status() == {
        "new_relic": {"status": "ok"},
        "langsmith": {"status": "degraded", "detail": "timeout"},
    }
