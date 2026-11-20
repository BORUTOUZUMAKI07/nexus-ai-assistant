"""RAG/file search must include long-term memories alongside document hits.

``POST /files/rag/query`` returned only Qdrant citations. A user who had told
the assistant a preference in chat got no personalisation from the file-search
endpoint. The document results stay untouched; Mem0 context is returned as a
separate, optional field so existing clients are unaffected.
"""
from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest
from backend.app.api.v1 import files
from backend.app.domain.file.schemas import RAGQueryRequest, RAGQueryResult

MEMORY_BLOCK = (
    "### Relevant User Context (from previous conversations):\n"
    "- User prefers dark mode in all tools\n"
)


async def _value(result):
    return result


def _rag_result():
    return RAGQueryResult(
        query="What do we use?",
        citations=[],
        total_retrieved=0,
        query_variants=["What do we use?"],
    )


@pytest.mark.asyncio
async def test_rag_query_returns_mem0_context(monkeypatch):
    """RED: file search must expose relevant memories alongside citations."""
    recalled = []

    async def _recall(**kwargs):
        recalled.append(kwargs)
        return MEMORY_BLOCK

    async def _query(**_kwargs):
        return _rag_result()

    monkeypatch.setattr(files.long_term_memory, "build_memory_context_block", _recall)

    user_id = uuid4()
    result = await files.query_rag(
        rag_in=RAGQueryRequest(query="What do we use?"),
        current_user=SimpleNamespace(id=user_id),
        rag_svc=SimpleNamespace(query=_query),
    )

    assert result.memory_context == MEMORY_BLOCK
    assert recalled[0]["user_id"] == user_id
    assert recalled[0]["query"] == "What do we use?"


@pytest.mark.asyncio
async def test_rag_query_fails_open_when_memory_is_unavailable(monkeypatch):
    """A Mem0 outage must not break file search."""

    async def _recall(**_kwargs):
        raise RuntimeError("mem0 down")

    async def _query(**_kwargs):
        return _rag_result()

    monkeypatch.setattr(files.long_term_memory, "build_memory_context_block", _recall)

    result = await files.query_rag(
        rag_in=RAGQueryRequest(query="What do we use?"),
        current_user=SimpleNamespace(id=uuid4()),
        rag_svc=SimpleNamespace(query=_query),
    )

    assert result.query == "What do we use?"
    assert result.memory_context == ""


@pytest.mark.asyncio
async def test_rag_query_accepts_a_dict_shaped_service_result(monkeypatch):
    """A plain-dict RAG result must still get memories, not a 500."""
    async def _recall(**_kwargs):
        return MEMORY_BLOCK

    async def _query(**_kwargs):
        return {
            "query": "What do we use?",
            "citations": [],
            "total_retrieved": 0,
            "query_variants": ["What do we use?"],
        }

    monkeypatch.setattr(files.long_term_memory, "build_memory_context_block", _recall)

    result = await files.query_rag(
        rag_in=RAGQueryRequest(query="What do we use?"),
        current_user=SimpleNamespace(id=uuid4()),
        rag_svc=SimpleNamespace(query=_query),
    )

    assert result.query == "What do we use?"
    assert result.memory_context == MEMORY_BLOCK
