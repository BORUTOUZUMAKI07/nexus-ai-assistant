"""
Tests for the Prometheus /metrics surface (T-08): zero-dependency text
exposition from the MetricsCollector plus the optional bearer-token gate.
"""
import pytest
from backend.app.services.observability.metrics import (
    MetricsCollector,
    _escape_label_value,
    render_prometheus_text,
)


def test_escape_label_value_handles_quotes_backslashes_newlines():
    assert _escape_label_value('a"b\\c\nd') == 'a\\"b\\\\c\\nd'


def test_render_emits_type_and_sample_lines():
    collector = MetricsCollector()
    collector.increment("tool_calls:web_search", 3)
    collector.record_error("tool_web_search_TimeoutError")
    for ms in (100, 200, 300, 400, 500):
        collector.record_latency("tool:web_search", float(ms))

    text = render_prometheus_text(collector)

    assert "# HELP nexus_requests_total" in text
    assert "# TYPE nexus_requests_total counter" in text
    assert 'nexus_requests_total{operation="tool_calls:web_search"} 3' in text

    assert "# TYPE nexus_errors_total counter" in text
    assert 'nexus_errors_total{error="tool_web_search_TimeoutError"} 1' in text

    # Latency family is a real Prometheus summary (seconds, quantiles + _sum/_count).
    assert "# TYPE nexus_latency_seconds summary" in text
    assert 'nexus_latency_seconds{operation="tool:web_search",quantile="0.5"} 0.3' in text
    assert 'nexus_latency_seconds{operation="tool:web_search",quantile="0.99"} 0.5' in text
    assert 'nexus_latency_seconds_count{operation="tool:web_search"} 5' in text
    assert 'nexus_latency_seconds_sum{operation="tool:web_search"} 1.5' in text
    assert 'nexus_latency_avg_seconds{operation="tool:web_search"} 0.3' in text


def test_render_empty_collector_has_only_help_and_type_metadata():
    text = render_prometheus_text(MetricsCollector())
    assert text.strip().endswith("# TYPE nexus_latency_avg_seconds gauge") or "# HELP nexus_requests_total" in text
    assert "nexus_requests_total{operation=" not in text  # no samples yet


def test_render_escapes_hostile_operation_names():
    collector = MetricsCollector()
    collector.increment('tool:evil"name\\with\nnewline')
    text = render_prometheus_text(collector)
    assert 'operation="tool:evil\\"name\\\\with\\nnewline"' in text


@pytest.mark.asyncio
async def test_metrics_endpoint_open_when_no_token_configured(monkeypatch):
    from backend.app.core.config import settings
    from backend.app.main import app
    from httpx import ASGITransport, AsyncClient

    monkeypatch.setattr(settings, "METRICS_TOKEN", None)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.get("/metrics")

    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/plain")
    assert "# TYPE nexus_requests_total counter" in resp.text


@pytest.mark.asyncio
async def test_metrics_endpoint_requires_bearer_token_when_configured(monkeypatch):
    from backend.app.core.config import settings
    from backend.app.main import app
    from httpx import ASGITransport, AsyncClient

    monkeypatch.setattr(settings, "METRICS_TOKEN", "scrape-secret-123")

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        denied = await ac.get("/metrics")
        assert denied.status_code == 401
        assert denied.headers.get("www-authenticate") == "Bearer"

        allowed = await ac.get(
            "/metrics", headers={"Authorization": "Bearer scrape-secret-123"}
        )
        assert allowed.status_code == 200
        assert "# TYPE nexus_requests_total counter" in allowed.text
