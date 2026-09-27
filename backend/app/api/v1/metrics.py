"""
Prometheus /metrics endpoint — the SLO + alerting surface (T-08).

Zero-dependency exposition of the in-process ``MetricsCollector`` in the
Prometheus text format (v0.0.4). Guarded by an optional shared bearer token
(``METRICS_TOKEN``): when set, scrapers must send
``Authorization: Bearer <METRICS_TOKEN>``; when unset the endpoint is open for
local/dev scraping (always configure the token in production — see
docs/slo.md → alerting).
"""
import secrets

import structlog
from backend.app.core.config import settings
from backend.app.services.observability.metrics import render_prometheus_text
from fastapi import APIRouter, HTTPException, Request, Response, status

logger = structlog.get_logger(__name__)

router = APIRouter(tags=["metrics"])


def _authorized(request: Request) -> bool:
    """Constant-time bearer-token check; open when no token is configured."""
    token = settings.METRICS_TOKEN
    if not token:
        return True
    return secrets.compare_digest(
        request.headers.get("Authorization", ""), f"Bearer {token}"
    )


@router.get("/metrics", include_in_schema=False)
async def prometheus_metrics(request: Request) -> Response:
    """Expose the in-process metrics in Prometheus text exposition format."""
    if not _authorized(request):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing metrics bearer token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return Response(
        content=render_prometheus_text(),
        media_type="text/plain; version=0.0.4; charset=utf-8",
    )
