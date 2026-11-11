"""
Integration tests for FastAPI application endpoints, RAG chunking, and memory service.
"""
import backend.app.main as main_module
import pytest
from backend.app.main import app
from backend.app.services.rag.chunking import chunking_service
from httpx import ASGITransport, AsyncClient


@pytest.mark.asyncio
async def test_health_endpoint():
    """Verify system health endpoint returns 200 and valid JSON."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        response = await ac.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "healthy"
    assert "Nexus AI Assistant" in data["service"]


@pytest.mark.asyncio
async def test_root_endpoint():
    """Verify root endpoint returns welcome message."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        response = await ac.get("/")
    assert response.status_code == 200
    data = response.json()
    assert "Nexus AI Assistant" in data["message"]


def test_chunking_service_markdown_headers():
    """Verify header-aware chunking correctly preserves sections."""
    doc = """# Introduction
Nexus AI is a modern multi-agent system.

## Architecture
It uses LangGraph for orchestration and Qdrant for vector search.

## Free Tier Strategy
Groq and OpenRouter provide zero-cost LLM compute.
"""
    chunks = chunking_service.chunk_document(doc, source_metadata={"doc_id": "test-1"})
    assert len(chunks) >= 2
    headers = [c.metadata.get("header") for c in chunks]
    assert any("Architecture" in (h or "") for h in headers)

@pytest.mark.asyncio
async def test_readiness_endpoint_returns_ready_when_database_is_available(monkeypatch):
    async def healthy():
        return True

    async def reachable():
        return True

    monkeypatch.setattr(main_module, "check_database_health", healthy)
    monkeypatch.setattr(main_module.redis_client, "ping", reachable)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        response = await ac.get("/health/ready")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ready",
        "database": "available",
        "mcp_transport": "not_started",
        "rate_limit_store": "redis",
    }


@pytest.mark.asyncio
async def test_readiness_reports_degraded_rate_limit_when_redis_is_unreachable(monkeypatch):
    """A deployed origin must make per-instance rate limiting visible, not just log it.

    The limiter falls back to an in-process window when Redis is unreachable. That
    keeps auth available, but the ceiling is then per-instance: on a multi-instance
    deployment brute-force protection is weaker than it looks. Readiness reports it
    so the check is a request, not a log hunt.
    """

    async def healthy():
        return True

    async def unreachable():
        raise ConnectionError("upstash unreachable")

    monkeypatch.setattr(main_module, "check_database_health", healthy)
    monkeypatch.setattr(main_module.redis_client, "ping", unreachable)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        response = await ac.get("/health/ready")

    # Still ready: serving traffic is not the same as being fully protected.
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ready"
    assert body["rate_limit_store"] == "in_process"


@pytest.mark.asyncio
async def test_readiness_endpoint_returns_503_when_database_is_unavailable(monkeypatch):
    async def unhealthy():
        return False

    monkeypatch.setattr(main_module, "check_database_health", unhealthy)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        response = await ac.get("/health/ready")

    assert response.status_code == 503
    assert response.json() == {"status": "not_ready", "database": "unavailable"}

