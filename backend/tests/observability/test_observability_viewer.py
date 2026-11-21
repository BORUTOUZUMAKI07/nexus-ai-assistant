"""
Unit tests for the admin observability viewer (closes the "no viewer" gap).

Covers the data gatherer against the shared in-memory FakeSession, the
self-contained HTML renderer (escaping included), and the admin route wiring
(200/403) via ASGI dependency overrides — no database, no network.
"""
import uuid

import pytest
from backend.app.api import deps
from backend.app.domain.usage.models import CostLog, EvaluationLog, UsageLog
from backend.app.main import app
from backend.app.services.observability import viewer as viewer_module
from backend.app.services.observability.viewer import (
    gather_viewer_data,
    observability_stack_status,
    render_viewer_html,
)
from backend.tests.fakes import FakeSession
from httpx import ASGITransport, AsyncClient


def _fake_admin():
    return type("Admin", (), {"id": uuid.uuid4(), "role": "admin"})()


async def _override_db(fake: FakeSession):
    async def _gen():
        yield fake

    return _gen


def _seed_viewer(fake: FakeSession) -> None:
    uid = uuid.uuid4()
    fake.seed(
        UsageLog,
        [
            UsageLog(
                user_id=uid,
                model="groq/llama-3.3-70b-versatile",
                provider="groq",
                prompt_tokens=100,
                completion_tokens=200,
                total_tokens=300,
                latency_ms=120.0,
                cost_usd=0.0003,
                status="success",
            ),
            UsageLog(
                user_id=uid,
                model="groq/llama-3.3-70b-versatile",
                provider="groq",
                prompt_tokens=50,
                completion_tokens=50,
                total_tokens=100,
                latency_ms=300.0,
                cost_usd=0.0002,
                status="error",
            ),
        ],
    )
    fake.seed(
        CostLog,
        [
            CostLog(
                user_id=uid,
                provider="groq",
                model="groq/llama-3.3-70b-versatile",
                input_cost=0.0001,
                output_cost=0.0002,
                total_cost=0.0003,
                billing_period="2026-09",
            ),
            CostLog(
                user_id=uid,
                provider="groq",
                model="groq/llama-3.3-70b-versatile",
                input_cost=0.0001,
                output_cost=0.0001,
                total_cost=0.0002,
                billing_period="2026-09",
            ),
        ],
    )
    fake.seed(
        EvaluationLog,
        [
            EvaluationLog(trace_id="t-1", metric_name="faithfulness", score=0.9, passed=True, evaluator="heuristic", reason="grounded"),
            EvaluationLog(trace_id="t-2", metric_name="faithfulness", score=0.4, passed=False, evaluator="heuristic", reason="hallucinated <script>alert(1)</script>"),
            EvaluationLog(trace_id="t-3", metric_name="answer_relevancy", score=0.95, passed=True, evaluator="heuristic", reason="on-topic"),
        ],
    )


@pytest.mark.asyncio
async def test_gather_viewer_data_rolls_up_cost_usage_and_evals():
    fake = FakeSession()
    _seed_viewer(fake)

    data = await gather_viewer_data(fake)

    # cost rollups
    assert data["cost"]["log_count"] == 2
    assert abs(data["cost"]["total_usd"] - 0.0005) < 1e-9
    assert len(data["cost"]["by_model"]) == 1
    assert data["cost"]["by_model"][0]["model"] == "groq/llama-3.3-70b-versatile"
    assert data["cost"]["by_model"][0]["calls"] == 2
    assert data["cost"]["by_period"][0]["period"] == "2026-09"

    # usage telemetry
    assert data["usage"]["request_count"] == 2
    assert data["usage"]["error_count"] == 1
    assert abs(data["usage"]["error_rate"] - 0.5) < 1e-6
    assert data["usage"]["latency"]["count"] == 2
    assert data["usage"]["latency"]["avg_ms"] == 210.0
    assert data["usage"]["latency"]["p99_ms"] > 0
    assert data["usage"]["by_model"][0]["tokens"] == 400
    assert len(data["usage"]["recent"]) == 2

    # evaluation scorecard
    assert data["evaluations"]["log_count"] == 3
    by_metric = {m["metric"]: m for m in data["evaluations"]["by_metric"]}
    assert by_metric["faithfulness"]["count"] == 2
    assert by_metric["faithfulness"]["passed"] == 1
    assert abs(by_metric["faithfulness"]["pass_rate"] - 0.5) < 1e-6

    # drift section present and gracefully shaped
    assert "detected" in data["drift"]
    assert "as_of" in data
    assert data["stack"]["metrics_collector"] is True


@pytest.mark.asyncio
async def test_gather_viewer_data_empty_never_raises():
    data = await gather_viewer_data(FakeSession())
    assert data["cost"]["total_usd"] == 0.0
    assert data["cost"]["by_model"] == []
    assert data["usage"]["request_count"] == 0
    assert data["usage"]["error_rate"] == 0.0
    assert data["usage"]["latency"] is None
    assert data["evaluations"]["by_metric"] == []
    assert data["drift"]["detected"] is False


@pytest.mark.asyncio
async def test_render_viewer_html_contains_sections_and_escapes():
    fake = FakeSession()
    _seed_viewer(fake)

    data = await gather_viewer_data(fake)
    html_out = render_viewer_html(data)

    assert "<title>Nexus · Observability</title>" in html_out
    assert "Stack status" in html_out
    assert "Cost · total $0.000500" in html_out
    assert "Drift report" in html_out
    assert "Evaluation scorecard" in html_out
    assert "Recent requests" in html_out
    # user/serialized strings must be escaped — the seeded reason had a <script>
    assert "<script>alert(1)</script>" not in html_out
    assert "&lt;script&gt;" in html_out
    # zero external resources: no remote src/href in the page (self-contained)
    assert 'src="http' not in html_out and 'href="http' not in html_out


def test_observability_stack_status_reports_newrelic_honestly():
    stack = observability_stack_status()
    assert "newrelic" in stack
    assert stack["newrelic"]["status"] in ("live", "inactive", "pending", "configured", "degraded")
    assert stack["drift_monitoring"]["enabled"] is True
    assert "activate" in stack["newrelic"]


def _configure_new_relic(monkeypatch, startup_status, *, resolvable=True, credentials=True):
    monkeypatch.setattr(viewer_module.settings, "NEW_RELIC_ENABLED", True)
    monkeypatch.setattr(
        viewer_module.settings, "NEW_RELIC_OTLP_ENDPOINT", "http://collector:4318"
    )
    monkeypatch.setattr(
        viewer_module.settings, "OTLP_AUTH_USER", "user" if credentials else ""
    )
    monkeypatch.setattr(
        viewer_module.settings, "OTLP_AUTH_PASSWORD", "pass" if credentials else ""
    )
    startup = {"status": startup_status}
    if startup_status == "degraded":
        startup["detail"] = "flush_failed"
    monkeypatch.setattr(
        viewer_module,
        "get_startup_telemetry_status",
        lambda: {"new_relic": startup},
        raising=False,
    )
    monkeypatch.setattr(
        viewer_module, "collector_endpoint_resolvable", lambda: resolvable, raising=False
    )


def test_config_alone_is_not_reported_as_live(monkeypatch):
    """RED: a failed startup flush must not produce a green 'live' badge."""
    _configure_new_relic(monkeypatch, "degraded")

    stack = observability_stack_status()

    assert stack["newrelic"]["status"] != "live"
    assert stack["newrelic"]["status"] == "degraded"
    assert "flush_failed" in str(stack["newrelic"])


@pytest.mark.asyncio
async def test_failed_flush_is_not_green_in_the_viewer(monkeypatch):
    _configure_new_relic(monkeypatch, "degraded")
    html_out = render_viewer_html(await gather_viewer_data(FakeSession()))

    assert '<b class="badge ok">live</b>' not in html_out
    assert "degraded" in html_out


def test_live_requires_startup_probe_and_resolvable_collector(monkeypatch):
    _configure_new_relic(monkeypatch, "ok")
    assert observability_stack_status()["newrelic"]["status"] == "live"


def test_pending_startup_is_not_live(monkeypatch):
    _configure_new_relic(monkeypatch, "pending")
    status = observability_stack_status()["newrelic"]["status"]
    assert status in ("pending", "configured")
    assert status != "live"


def test_unresolvable_collector_is_degraded(monkeypatch):
    _configure_new_relic(monkeypatch, "ok", resolvable=False)
    assert observability_stack_status()["newrelic"]["status"] == "degraded"


def test_missing_credentials_are_degraded(monkeypatch):
    _configure_new_relic(monkeypatch, "ok", credentials=False)
    assert observability_stack_status()["newrelic"]["status"] == "degraded"


def test_disabled_new_relic_is_inactive(monkeypatch):
    monkeypatch.setattr(viewer_module.settings, "NEW_RELIC_ENABLED", False)
    assert observability_stack_status()["newrelic"]["status"] == "inactive"


@pytest.fixture
async def admin_client():
    fake = FakeSession()
    _seed_viewer(fake)

    async def _db():
        yield fake

    async def _admin():
        return _fake_admin()

    app.dependency_overrides[deps.get_db] = _db
    app.dependency_overrides[deps.get_current_admin] = _admin
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    app.dependency_overrides.pop(deps.get_db, None)
    app.dependency_overrides.pop(deps.get_current_admin, None)


@pytest.mark.asyncio
async def test_viewer_html_route_served_to_admin(admin_client):
    r = await admin_client.get("/api/v1/admin/monitoring/viewer")
    assert r.status_code == 200
    assert "text/html" in r.headers.get("content-type", "")
    assert "Nexus · Observability" in r.text


@pytest.mark.asyncio
async def test_viewer_data_route_returns_json(admin_client):
    r = await admin_client.get("/api/v1/admin/monitoring/viewer-data")
    assert r.status_code == 200
    body = r.json()
    assert {"stack", "cost", "usage", "drift", "evaluations", "telemetry"} <= set(body)


@pytest.mark.asyncio
async def test_viewer_route_denies_non_admin():
    from fastapi import HTTPException

    async def _db():
        yield FakeSession()

    async def _deny():
        raise HTTPException(status_code=403, detail="The user doesn't have enough privileges")

    app.dependency_overrides[deps.get_db] = _db
    app.dependency_overrides[deps.get_current_admin] = _deny
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            r = await ac.get("/api/v1/admin/monitoring/viewer")
        assert r.status_code == 403
    finally:
        app.dependency_overrides.pop(deps.get_db, None)
        app.dependency_overrides.pop(deps.get_current_admin, None)
