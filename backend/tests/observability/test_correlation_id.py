"""Regression tests for request correlation IDs on the real ASGI app."""

import uuid

import pytest
import structlog
from backend.app.main import app
from httpx import ASGITransport, AsyncClient

HEADER = "X-Correlation-ID"
CTX_PATH = "/__correlation_context_probe"
ERROR_PATH = "/__correlation_error_probe"


def _is_uuid(value: str | None) -> bool:
    if not value:
        return False
    try:
        uuid.UUID(value)
    except ValueError:
        return False
    return True


@pytest.fixture
async def client():
    # ServerErrorMiddleware re-raises after sending its 500 response; keep the
    # response for assertions instead of letting httpx turn it into an exception.
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


async def test_health_response_carries_generated_correlation_id(client):
    response = await client.get("/health")

    assert response.status_code == 200
    assert _is_uuid(response.headers.get(HEADER))


async def test_valid_caller_correlation_id_is_preserved(client):
    caller_id = str(uuid.uuid4())

    response = await client.get("/health", headers={HEADER: caller_id})

    assert response.headers.get(HEADER) == caller_id


async def test_malformed_caller_correlation_id_is_replaced(client):
    response = await client.get("/health", headers={HEADER: "not-a-uuid"})

    correlation_id = response.headers.get(HEADER)
    assert _is_uuid(correlation_id)
    assert correlation_id != "not-a-uuid"


async def test_correlation_id_is_bound_to_log_context_and_cleared(client):
    async def context_probe() -> dict[str, str | None]:
        return {"request_id": structlog.contextvars.get_contextvars().get("request_id")}

    app.add_api_route(CTX_PATH, context_probe, methods=["GET"])
    try:
        response = await client.get(CTX_PATH)
        assert response.status_code == 200
        request_id = response.json()["request_id"]
        assert _is_uuid(request_id)
        assert request_id == response.headers.get(HEADER)
        assert "request_id" not in structlog.contextvars.get_contextvars()
    finally:
        app.router.routes[:] = [
            route for route in app.router.routes if getattr(route, "path", None) != CTX_PATH
        ]


async def test_unhandled_error_response_carries_correlation_id(client):
    async def boom() -> None:
        raise RuntimeError("correlation probe boom")

    app.add_api_route(ERROR_PATH, boom, methods=["GET"])
    try:
        response = await client.get(ERROR_PATH)
        assert response.status_code == 500
        assert _is_uuid(response.headers.get(HEADER))
    finally:
        app.router.routes[:] = [
            route for route in app.router.routes if getattr(route, "path", None) != ERROR_PATH
        ]
