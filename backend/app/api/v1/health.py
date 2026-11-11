"""
Health check endpoint.

Returns 200 when the FastAPI process is up. No authentication required -- this
is called by the BFF's /api/health route to verify the backend is reachable, and
by the hosting platform's load balancer directly.
"""
from fastapi import APIRouter

router = APIRouter()


@router.get("/health")
async def health() -> dict[str, str]:
    """Liveness probe: the process is running if this answers."""
    return {"status": "ok", "service": "nexus-backend"}
