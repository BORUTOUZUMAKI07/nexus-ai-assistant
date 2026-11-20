"""/health/dependencies must probe Mem0, not just inspect the client object.

The old row reported ``ok`` whenever a Mem0 client existed. That meant a key
revoked at the provider still looked healthy until the next restart (the SDK
pings only at construction). The probe is read-only, bounded by the existing
health timeout, and cached with the rest of the dependency snapshot.
"""
from __future__ import annotations

import pytest
from backend.app.services.health import dependencies

pytestmark = pytest.mark.asyncio


class MemoryClientFake:
    """Stands in for mem0's AsyncMemoryClient without holding a key."""


class _FakeResponse:
    def __init__(self, status_code: int) -> None:
        self.status_code = status_code


class _FakeAsyncClient:
    last_headers: dict[str, str] = {}
    status_code = 200

    def __init__(self, **_kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False

    async def get(self, url, *, headers):
        type(self).last_headers = headers
        return _FakeResponse(self.status_code)


async def _with_cloud_client(monkeypatch, status_code: int):
    monkeypatch.setattr(dependencies.long_term_memory, "_client", MemoryClientFake())
    monkeypatch.setattr(dependencies.settings, "MEM0_API_KEY", "test-key-not-a-secret")
    monkeypatch.setattr(dependencies.httpx, "AsyncClient", _FakeAsyncClient)
    monkeypatch.setattr(_FakeAsyncClient, "status_code", status_code)


async def test_mem0_probe_pings_the_api(monkeypatch):
    """RED: a healthy response should be based on a real ping."""
    await _with_cloud_client(monkeypatch, 200)

    result = await dependencies._probe_mem0(5.0)

    assert result["status"] == "ok"
    assert result["mode"] == "cloud"
    assert _FakeAsyncClient.last_headers["Authorization"].startswith("Token ")


async def test_mem0_probe_reports_a_failed_ping_as_degraded(monkeypatch):
    """RED: a revoked key must not still look healthy."""
    await _with_cloud_client(monkeypatch, 401)

    result = await dependencies._probe_mem0(5.0)

    assert result["status"] == "degraded"
    assert result["detail"] == "ping_failed"
    assert result["mode"] == "cloud"


async def test_mem0_probe_skips_network_when_not_configured(monkeypatch):
    """No key means no ping and no secret in the response."""
    monkeypatch.setattr(dependencies.long_term_memory, "_client", None)
    monkeypatch.setattr(dependencies.settings, "MEM0_API_KEY", None)

    result = await dependencies._probe_mem0(5.0)

    assert result["status"] == "not_configured"
    assert result["mode"] == "noop"
    assert "Authorization" not in result
