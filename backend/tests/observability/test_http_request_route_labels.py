"""HTTP request metrics must be labelled by route template, never raw path.

A raw path embeds conversation/message/file IDs. Labelling a counter or
histogram with it mints a new time series per object and turns a cheap RED
dashboard into an unbounded (and billable) series explosion. The router knows
the matched template in ``scope["route"]``, so that is what the metrics use;
anything that did not match a route is one constant ``unmatched`` series.

These tests are deliberately about the *application's* call path: a helper in
isolation would not catch a middleware that forgets to pass the scope.
"""
from types import SimpleNamespace
from typing import Any

import backend.app.main as main_mod
import pytest
from backend.app.services.observability import metrics as metrics_mod
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from starlette.middleware.base import BaseHTTPMiddleware

UUID_A = "9f8e7d6c-5b4a-4c3d-8e2f-1a0b9c8d7e6f"
UUID_B = "12345678-90ab-4cde-8f01-234567890abc"
TEMPLATE = "/api/v1/conversations/{conversation_id}"


def _recording_meter(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, float, dict]]:
    """Capture the attributes the collector hands to OTel instruments."""
    records: list[tuple[str, float, dict]] = []

    class _Counter:
        def __init__(self, name: str) -> None:
            self.name = name

        def add(self, amount: float, attributes: dict | None = None) -> None:
            records.append((self.name, amount, dict(attributes or {})))

    class _Histogram:
        def __init__(self, name: str) -> None:
            self.name = name

        def record(self, amount: float, attributes: dict | None = None) -> None:
            records.append((self.name, amount, dict(attributes or {})))

    class _Meter:
        def create_counter(self, name: str, **_kwargs: Any) -> _Counter:
            return _Counter(name)

        def create_histogram(self, name: str, **_kwargs: Any) -> _Histogram:
            return _Histogram(name)

    monkeypatch.setattr(metrics_mod, "get_meter", lambda _name: _Meter())
    return records


def _app_with_http_metrics() -> FastAPI:
    """Minimal app using the exact middleware registered by the real app."""
    app = FastAPI()
    app.add_middleware(BaseHTTPMiddleware, dispatch=main_mod.otlp_http_span_middleware)

    @app.get(TEMPLATE)
    async def get_conversation(conversation_id: str) -> dict[str, str]:
        return {"id": conversation_id}

    @app.get("/api/v1/boom")
    async def boom() -> dict[str, str]:
        raise RuntimeError("boom")

    return app


def test_route_template_uses_the_matched_template_not_the_raw_uuid_path():
    scope = {
        "route": SimpleNamespace(path=TEMPLATE),
        "path": f"/api/v1/conversations/{UUID_A}",
    }

    assert metrics_mod.route_template(scope) == TEMPLATE
    assert UUID_A not in metrics_mod.route_template(scope)


def test_route_template_is_one_constant_for_unmatched_requests():
    # 404s and pre-routing failures must not mint a series per attempted URL.
    for scope in (
        {},
        {"route": None, "path": f"/api/v1/conversations/{UUID_A}"},
        {"route": object(), "path": "/api/v1/definitely-not-a-route/12345"},
    ):
        assert metrics_mod.route_template(scope) == metrics_mod.UNMATCHED_ROUTE


def test_status_class_is_a_bounded_label():
    assert metrics_mod.status_class(200) == "2xx"
    assert metrics_mod.status_class(302) == "3xx"
    assert metrics_mod.status_class(404) == "4xx"
    assert metrics_mod.status_class(503) == "5xx"


def test_record_http_request_sends_only_bounded_otel_labels(monkeypatch):
    records = _recording_meter(monkeypatch)
    collector = metrics_mod.MetricsCollector()

    metrics_mod.record_http_request("GET", TEMPLATE, 503, 30.0, collector=collector)

    attrs = [attributes for _name, _value, attributes in records]
    assert attrs, "the collector must forward HTTP samples to OTel"
    assert all(attribute["http.route"] == TEMPLATE for attribute in attrs)
    assert all(attribute["http.request.method"] == "GET" for attribute in attrs)
    assert any(
        attribute.get("http.response.status_class") == "5xx" for attribute in attrs
    )
    assert UUID_A not in str(attrs)
    # The in-process keys stay stable: /metrics consumers and existing SLOs use
    # them, and an exact status code there is bounded by HTTP itself.
    assert collector._counts["http_requests_total"] == 1
    assert collector._latencies["http_request"] == [30.0]
    assert collector._errors["http_503"] == 1


@pytest.mark.asyncio
async def test_uuid_heavy_paths_collapse_to_one_route_series(monkeypatch):
    records = _recording_meter(monkeypatch)
    collector = metrics_mod.MetricsCollector()
    monkeypatch.setattr(main_mod, "metrics_collector", collector)

    transport = ASGITransport(app=_app_with_http_metrics())
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        assert (await ac.get(f"/api/v1/conversations/{UUID_A}")).status_code == 200
        assert (await ac.get(f"/api/v1/conversations/{UUID_B}")).status_code == 200

    requests = [
        (value, attributes)
        for name, value, attributes in records
        if name == "nexus_requests_total"
    ]
    assert len(requests) == 2
    assert sum(value for value, _attributes in requests) == 2
    assert {attributes["http.route"] for _value, attributes in requests} == {TEMPLATE}
    assert UUID_A not in str(requests)
    assert UUID_B not in str(requests)


@pytest.mark.asyncio
async def test_unmatched_path_is_labelled_unmatched(monkeypatch):
    records = _recording_meter(monkeypatch)
    collector = metrics_mod.MetricsCollector()
    monkeypatch.setattr(main_mod, "metrics_collector", collector)

    transport = ASGITransport(app=_app_with_http_metrics())
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        assert (await ac.get(f"/api/v1/not-a-route/{UUID_A}")).status_code == 404

    requests = [
        attributes
        for name, _value, attributes in records
        if name == "nexus_requests_total"
    ]
    assert requests
    assert all(attributes["http.route"] == metrics_mod.UNMATCHED_ROUTE for attributes in requests)
    assert UUID_A not in str(requests)


@pytest.mark.asyncio
async def test_raising_handler_still_records_a_5xx_sample(monkeypatch):
    records = _recording_meter(monkeypatch)
    collector = metrics_mod.MetricsCollector()
    monkeypatch.setattr(main_mod, "metrics_collector", collector)

    transport = ASGITransport(app=_app_with_http_metrics())
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        with pytest.raises(RuntimeError, match="boom"):
            await ac.get("/api/v1/boom")

    errors = [
        attributes
        for name, _value, attributes in records
        if name == "nexus_errors_total"
    ]
    assert errors
    assert all(
        attributes.get("http.response.status_class") == "5xx" for attributes in errors
    )
    assert all(attributes.get("http.route") == "/api/v1/boom" for attributes in errors)
    assert collector._latencies["http_request"]
