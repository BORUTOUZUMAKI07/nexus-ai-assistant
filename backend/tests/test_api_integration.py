"""
Integration tests for FastAPI application endpoints, RAG chunking, and memory service.
"""
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
