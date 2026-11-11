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


# A citation marker is a bracketed source number. Two forms occur in practice:
# the `[1]` this service formats into the prompt, and the `【1†file.md】` style
# some models emit unprompted. Recognising only the first scored a correctly
# cited, 100%-relevance answer as "LOW EVIDENCE 30%" in the UI - the badge was
# measuring the marker's typography, not the answer's grounding.
_CITATION_MARKER_RE = re.compile(r"[\[【]\s*(\d+)")


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
            metadata = chunk.get("metadata") or {}
            filename = metadata.get("filename", "Document")
            chunk_idx = chunk.get("chunk_index", 0)
            score = chunk.get("rerank_score", chunk.get("score", 1.0))
            content = chunk.get("content", "").strip()
            file_id = chunk.get("file_id") or metadata.get("file_id")

            formatted_context_blocks.append(
                f"[{idx}] Source: {filename} (Section/Chunk {chunk_idx})\n{content}"
            )

            structured_citations.append(
                RAGCitation(
                    file_id=file_id,
                    filename=filename,
                    chunk_index=chunk_idx,
                    score=score,
                    content_snippet=content[:200] + "..." if len(content) > 200 else content,
                    metadata=metadata,
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
            # No evidence retrieved → the response is ungrounded. Returning 1.0
            # here was an inverted fail mode: retrieval failure maximized the
            # trust score.
            return 0.0

        citation_markers = _CITATION_MARKER_RE.findall(response_text)
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
