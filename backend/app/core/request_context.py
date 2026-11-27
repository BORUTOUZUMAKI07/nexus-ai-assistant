"""Per-request correlation IDs bound into the structlog context.

The ID travels in the ``X-Correlation-ID`` response header and is bound as
``request_id`` on every log line emitted while handling the request. A caller
may supply a valid UUID; malformed or missing values get a fresh UUID instead.
"""
from collections.abc import Mapping
from uuid import UUID, uuid4

from backend.app.core.logging import bind_request_context, clear_request_context
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

CORRELATION_ID_HEADER = "X-Correlation-ID"


def resolve_correlation_id(headers: Mapping[str, str]) -> str:
    """Return a valid caller-supplied UUID, otherwise generate one."""
    supplied = headers.get(CORRELATION_ID_HEADER)
    if supplied:
        try:
            return str(UUID(supplied))
        except (ValueError, AttributeError, TypeError):
            pass
    return str(uuid4())


class CorrelationIdMiddleware(BaseHTTPMiddleware):
    """Bind a correlation ID for the request and echo it on the response."""

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        correlation_id = resolve_correlation_id(request.headers)
        request.state.correlation_id = correlation_id
        bind_request_context(request_id=correlation_id)
        try:
            response = await call_next(request)
        except Exception:
            clear_request_context()
            raise
        response.headers[CORRELATION_ID_HEADER] = correlation_id
        clear_request_context()
        return response
