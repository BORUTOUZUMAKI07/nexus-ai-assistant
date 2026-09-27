"""
Unit tests for the calibrated confidence gate (composite score + decision).
"""
from dataclasses import dataclass

from backend.app.core.config import settings
from backend.app.services.confidence_service import ConfidenceService

# Default configured threshold used when samples are too few to calibrate.
DEFAULT_THRESHOLD = settings.CONFIDENCE_THRESHOLD


@dataclass
class FakeCitation:
    score: float
    content_snippet: str = "retrieved evidence about quantum computing"


def _service() -> ConfidenceService:
    return ConfidenceService()


def test_ungrounded_chat_is_confident_and_neutral():
    decision = _service().decide("Hello! How can I help you today?")
    assert decision["grounded"] is False
    assert decision["confidence"] == 0.95
    assert decision["pass"] is True
    assert decision["action"] == "answer"
    assert decision["calibrated"] is False
    assert decision["threshold"] == DEFAULT_THRESHOLD


def test_grounded_composite_ties_confidence_to_evidence():
    svc = _service()
    grounded_ok = svc.score(
        "Quantum computing uses qubits and superposition for computation.",
        citations=[
            FakeCitation(score=0.9, content_snippet="Quantum computing uses qubits and superposition."),
        ],
    )
    grounded_low = svc.score(
        "The answer is 42 because of orbital mechanics.",
        citations=[FakeCitation(score=0.4, content_snippet="Quantum computing uses qubits.")],
    )
    assert grounded_ok["grounded"] is True
    assert grounded_ok["confidence"] >= 0.7
    assert grounded_low["confidence"] < grounded_ok["confidence"]


def test_grounding_score_forces_hedge_when_below_threshold():
    # A plausible-sounding answer with almost zero overlap with weak citations
    # must fall below the default threshold and produce a hedge decision.
    decision = _service().decide(
        "The answer is 42 because of orbital mechanics.",
        citations=[FakeCitation(score=0.15, content_snippet="unrelated filing system doc")],
    )
    assert decision["confidence"] < DEFAULT_THRESHOLD
    assert decision["pass"] is False
    assert decision["action"] == "hedge"


def test_dict_citations_are_tolerated():
    decision = _service().decide(
        "Quantum computing uses qubits.",
        citations=[
            {"score": 0.9, "content_snippet": "Quantum computing uses qubits and superposition."},
        ],
    )
    assert decision["grounded"] is True
    assert decision["confidence"] >= 0.7


def test_calibration_needs_minimum_sample_set():
    svc = _service()
    too_few = [{"confidence": 0.9, "outcome": 1.0} for _ in range(5)]
    threshold, calibrated = svc.calibrated_threshold(too_few)
    assert calibrated is False
    assert threshold == DEFAULT_THRESHOLD


def test_calibration_splits_positive_and_negative_means():
    svc = _service()
    samples = [{"confidence": 0.9, "outcome": 1.0} for _ in range(15)] + [
        {"confidence": 0.4, "outcome": 0.0} for _ in range(15)
    ]
    threshold, calibrated = svc.calibrated_threshold(samples)
    assert calibrated is True
    assert 0.4 < threshold < 0.9
    assert abs(threshold - 0.65) < 1e-6


def test_verbosity_penalizes_empty_and_wall_of_text():
    svc = _service()
    empty = svc.score("", citations=[FakeCitation(score=0.9)])
    wall = svc.score("word " * 450, citations=[FakeCitation(score=0.9)])
    normal = svc.score(
        "A concise grounded answer that covers the main points well enough.",
        citations=[FakeCitation(score=0.9)],
    )
    assert empty["verbosity"] == 0.0
    assert wall["verbosity"] < normal["verbosity"]
    assert normal["verbosity"] == 1.0
