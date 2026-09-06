"""
Evidence Gate Service.
Implements Pattern 23: Evidence Gates.
Enforces factual confidence thresholds (>=0.70) before claims are asserted in responses.
"""
from typing import Any
from uuid import UUID

import structlog
from backend.app.domain.file.schemas import RAGCitation
from backend.app.services.rag.citation import citation_service

logger = structlog.get_logger(__name__)

EVIDENCE_THRESHOLD = 0.70
NIL_UUID = UUID(int=0)


class EvidenceGate:
    """Verifies that synthesis responses meet strict factual support and confidence levels."""

    @staticmethod
    def verify_evidence_support(
        draft_response: str, retrieved_contexts: list[str]
    ) -> dict[str, Any]:
        """Calculates grounding and determines whether the evidence threshold is met."""
        if not retrieved_contexts:
            # If no context was retrieved, flag that the response is ungrounded
            return {
                "passed_gate": False,
                "confidence_score": 0.0,
                "citations": [],
                "reason": "No retrieved context available for grounding verification.",
            }

        citations = [
            RAGCitation(
                file_id=NIL_UUID,
                filename=f"context_{idx}",
                chunk_index=idx,
                score=1.0,
                content_snippet=ctx[:200] + ("..." if len(ctx) > 200 else ""),
                metadata={},
            )
            for idx, ctx in enumerate(retrieved_contexts, start=1)
        ]
        confidence = citation_service.verify_grounding(draft_response, citations)

        passed = confidence >= EVIDENCE_THRESHOLD

        logger.info(
            "evidence_gate_evaluated",
            confidence_score=confidence,
            threshold=EVIDENCE_THRESHOLD,
            passed=passed,
        )

        return {
            "passed_gate": passed,
            "confidence_score": confidence,
            "citations": citations,
            "reason": (
                "Sufficient empirical context backing generated claims."
                if passed
                else f"Factual confidence ({confidence:.2f}) below threshold ({EVIDENCE_THRESHOLD:.2f}). Refinement advised."
            ),
        }


evidence_gate = EvidenceGate()
