"""Dependency health: cached probes, honest statuses, no leaked credentials."""

import asyncio
import json
from types import SimpleNamespace

import backend.app.services.health.dependencies as deps
import pytest
from backend.app.main import app
from backend.app.services.observability.metrics import MetricsCollector
from httpx import ASGITransport, AsyncClient

EXPECTED_SERVICES = {
    "database",
    "redis",
    "qdrant",
    "mem0",
    "e2b",
    "firecrawl",
    "tavily",
    "new_relic",
    "langsmith",
    "storage",
    "email",
}
SECRET_MARKERS = ("secret-mem0-value", "secret-ls-value", "secret-sr-value", "secret-smtp-value")


class FakeSettings:
    MEM0_API_KEY = "secret-mem0-value"
    E2B_API_KEY = "e2b-secret"
    FIRECRAWL_API_KEY = "fc-secret"
    TAVILY_API_KEY = "tvly-secret"
    NEW_RELIC_ENABLED = True
    NEW_RELIC_OTLP_ENDPOINT = "https://collector.example.com"
    OTLP_AUTH_USER = "collector-user"
    OTLP_AUTH_PASSWORD = "collector-password"
    LANGSMITH_TRACING = True
    LANGSMITH_API_KEY = "secret-ls-value"
    SUPABASE_URL = "https://db.example.com"
    SUPABASE_SERVICE_ROLE_KEY = "secret-sr-value"
    STORAGE_BUCKET = "nexus-files"
    RESEND_API_KEY = None
    SMTP_USER = "user@example.com"
    SMTP_PASSWORD = "secret-smtp-value"
    HEALTH_DEPENDENCIES_CACHE_TTL_SECONDS = 30.0
    HEALTH_DEPENDENCIES_TIMEOUT_SECONDS = 2.0


@pytest.fixture(autouse=True)
def _isolated_dependency_cache(monkeypatch):
    monkeypatch.setattr(deps, "settings", FakeSettings())
    deps.clear_dependency_health_cache()
    yield
    deps.clear_dependency_health_cache()


@pytest.mark.asyncio
async def test_endpoint_serves_one_cached_snapshot(monkeypatch):
    calls = 0

    async def fake_collect():
        nonlocal calls
        calls += 1
        return {"status": "ok", "services": {"database": {"status": "ok"}}, "fallback_counters": {}}

    monkeypatch.setattr(deps, "_collect", fake_collect)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        first = await ac.get("/health/dependencies")
        second = await ac.get("/health/dependencies")

    assert first.status_code == 200
    assert first.json() == second.json()
    assert calls == 1, "two requests inside the TTL must not re-probe external services"


@pytest.mark.asyncio
async def test_cache_expires_after_ttl(monkeypatch):
    clock = [100.0]
    calls = 0

    async def fake_collect():
        nonlocal calls
        calls += 1
        return {"status": "ok", "services": {}, "fallback_counters": {"round": calls}}

    monkeypatch.setattr(deps, "_now", lambda: clock[0])
    monkeypatch.setattr(deps.settings, "HEALTH_DEPENDENCIES_CACHE_TTL_SECONDS", 5.0)
    monkeypatch.setattr(deps, "_collect", fake_collect)

    first = await deps.get_dependency_health()
    clock[0] += 10.0
    second = await deps.get_dependency_health()

    assert first["fallback_counters"] == {"round": 1}
    assert second["fallback_counters"] == {"round": 2}
    assert calls == 2


@pytest.mark.asyncio
async def test_collect_reports_every_service_without_credentials(monkeypatch):
    async def ok_db():
        return True

    async def ok_redis():
        return True

    async def ok_collections():
        return ["nexus_knowledge"]

    async def ok_storage(timeout):
        return {"status": "ok"}

    monkeypatch.setattr(deps, "check_database_health", ok_db)
    monkeypatch.setattr(deps.redis_client, "ping", ok_redis)
    monkeypatch.setattr(deps.vector_db.client, "get_collections", ok_collections)
    monkeypatch.setattr(deps, "_probe_storage", ok_storage)
    monkeypatch.setattr(deps, "collector_endpoint_resolvable", lambda: True)

    counters = MetricsCollector()
    counters.increment("dependency_fallback:mem0_add")
    counters.increment("http_requests_total")
    monkeypatch.setattr(deps, "metrics_collector", counters)

    result = await deps.get_dependency_health()
    body = json.dumps(result)

    assert result["status"] == "ok"
    assert set(result["services"]) == EXPECTED_SERVICES
    assert result["fallback_counters"] == {"dependency_fallback:mem0_add": 1}
    assert not [marker for marker in SECRET_MARKERS if marker in body], "response leaked a credential"


@pytest.mark.asyncio
async def test_collect_marks_degraded_when_a_probe_fails(monkeypatch):
    async def dead_db():
        raise ConnectionError("db down")

    async def ok_redis():
        return True

    async def ok_collections():
        return []

    async def ok_storage(timeout):
        return {"status": "ok"}

    monkeypatch.setattr(deps, "check_database_health", dead_db)
    monkeypatch.setattr(deps.redis_client, "ping", ok_redis)
    monkeypatch.setattr(deps.vector_db.client, "get_collections", ok_collections)
    monkeypatch.setattr(deps, "_probe_storage", ok_storage)
    monkeypatch.setattr(deps, "collector_endpoint_resolvable", lambda: True)

    result = await deps.get_dependency_health()

    assert result["status"] == "degraded"
    assert result["services"]["database"]["status"] == "degraded"
    assert result["services"]["redis"]["status"] == "ok"


@pytest.mark.asyncio
async def test_timed_probe_reports_timeout_instead_of_hanging():
    async def slow():
        await asyncio.sleep(0.1)

    result = await deps._timed("slow", slow, 0.01)

    assert result["status"] == "degraded"
    assert result["detail"] == "timeout"


def test_mem0_mode_is_reported_from_the_live_client(monkeypatch):
    class AsyncMemoryClient:
        pass

    monkeypatch.setattr(deps.long_term_memory, "_client", AsyncMemoryClient())
    assert deps._mem0_status()["mode"] == "cloud"
    assert deps._mem0_status()["status"] == "ok"

    monkeypatch.setattr(deps.long_term_memory, "_client", None)
    assert deps._mem0_status()["mode"] == "noop"
    assert deps._mem0_status()["status"] == "degraded"


def test_new_relic_is_degraded_when_collector_cannot_resolve(monkeypatch):
    monkeypatch.setattr(deps, "collector_endpoint_resolvable", lambda: False)
    result = deps._new_relic_status()
    assert result["status"] == "degraded"
    assert result["detail"] == "collector_unresolvable"
