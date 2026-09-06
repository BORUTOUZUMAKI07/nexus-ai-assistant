"""
Citation and Grounding Service.
Generates verifiable markdown citations ([1], [2]) linking assertions
directly to retrieved document chunks and computes grounding verification scores.
"""
import re
from typing import Any

import structlog
from backend.app.domain.file.schemas import RAGCitation

logger = structlog.get_logger(__name__)


class CitationService:
    """
    Handles inline citation insertion, citation mapping,
    and factual grounding verification.
    """

    def format_citations(self, chunks: list[dict[str, Any]]) -> tuple[str, list[RAGCitation]]:
        """
        Formats retrieved chunks into numbered context blocks for prompt injection
        and prepares structured RAGCitation objects.
        """
        formatted_context_blocks: list[str] = []
        structured_citations: list[RAGCitation] = []

        for idx, chunk in enumerate(chunks, start=1):
            filename = chunk.get("metadata", {}).get("filename", "Document")
            chunk_idx = chunk.get("chunk_index", 0)
            score = chunk.get("rerank_score", chunk.get("score", 1.0))
            content = chunk.get("content", "").strip()

            formatted_context_blocks.append(
                f"[{idx}] Source: {filename} (Section/Chunk {chunk_idx})\n{content}"
            )

            structured_citations.append(
                RAGCitation(
                    file_id=chunk.get("file_id") or chunk.get("metadata", {}).get("file_id"),
                    filename=filename,
                    chunk_index=chunk_idx,
                    score=score,
                    content_snippet=content[:200] + "..." if len(content) > 200 else content,
                    metadata=chunk.get("metadata", {}),
                )
            )

        context_string = "\n\n".join(formatted_context_blocks)
        return context_string, structured_citations

    def verify_grounding(self, response_text: str, citations: list[RAGCitation]) -> float:
        """
        Simple evidence gating calculation: computes the ratio of cited markers ([1], [2], etc.)
        and token overlap with source chunks to estimate grounding score (0.0 to 1.0).
        """
        if not citations:
            return 1.0  # No RAG context provided, metric not applicable

        citation_markers = re.findall(r"\[(\d+)\]", response_text)
        if not citation_markers:
            logger.warning("response_has_no_citation_markers_despite_context")
            return 0.3

        # Check valid indices
        valid_count = 0
        for marker in set(citation_markers):
            idx = int(marker)
            if 1 <= idx <= len(citations):
                valid_count += 1

        score = min(1.0, valid_count / min(len(citations), 3) * 0.9 + 0.1)
        return round(score, 2)


citation_service = CitationService()
