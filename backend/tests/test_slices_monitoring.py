"""
Unit tests for popularity-bucketed slice monitoring + the fairness surface.
"""
import uuid

import pytest
from backend.app.domain.conversation.models import Message
from backend.app.domain.usage.models import EvaluationLog, UsageLog
from backend.app.services.monitoring.slices_service import SliceService
from backend.tests.fakes import FakeSession


def _usage(model: str, provider: str, status: str = "success", latency_ms: float = 100.0) -> UsageLog:
    return UsageLog(
        user_id=uuid.uuid4(),
        model=model,
        provider=provider,
        latency_ms=latency_ms,
        cost_usd=0.001,
        status=status,
    )


def _message(model: str, feedback: str | None) -> Message:
    return Message(
        conversation_id=uuid.uuid4(),
        role="assistant",
        content="answer",
        model=model,
        user_feedback=feedback,
    )


@pytest.mark.asyncio
async def test_slice_report_groups_by_model_provider():
    fake = FakeSession()
    fake.seed(
        UsageLog,
        [
            _usage("alpha", "groq"),
            _usage("alpha", "groq", status="error", latency_ms=300.0),
            _usage("beta", "openai"),
            _usage("gamma", "groq", status="error"),
        ],
    )
    fake.seed(
        Message,
        [
            _message("alpha", "thumbs_up"),
            _message("alpha", "thumbs_up"),
            _message("beta", "thumbs_down"),
        ],
    )
    svc = SliceService(fake)

    report = await svc.slice_report()

    assert report["overall"]["requests"] == 4
    assert report["overall"]["error_rate"] == pytest.approx(0.5)
    assert report["overall"]["helpful_rate"] == pytest.approx(2 / 3, abs=0.001)
    by_key = {(s["model"], s["provider"]): s for s in report["slices"]}
    assert set(by_key) == {("alpha", "groq"), ("beta", "openai"), ("gamma", "groq")}
    assert by_key[("alpha", "groq")]["volume"] == 2
    assert by_key[("alpha", "groq")]["error_rate"] == pytest.approx(0.5)
    assert by_key[("alpha", "groq")]["avg_latency_ms"] == pytest.approx(200.0)
    assert by_key[("alpha", "groq")]["helpful_rate"] == pytest.approx(1.0)
    assert by_key[("beta", "openai")]["helpful_rate"] == pytest.approx(0.0)


@pytest.mark.asyncio
async def test_popularity_buckets_and_flags_high_volume_low_quality():
    fake = FakeSession()
    rows: list[UsageLog] = []
    for _ in range(10):
        rows.append(_usage("alpha", "groq"))
    for _ in range(8):
        rows.append(_usage("beta", "groq"))
    for _ in range(4):
        rows.append(_usage("gamma", "groq"))
    fake.seed(UsageLog, rows)
    fake.seed(
        Message,
        (
            [_message("alpha", "thumbs_up") for _ in range(5)]
            + [_message("alpha", "thumbs_down") for _ in range(5)]
            + [_message("beta", "thumbs_up") for _ in range(8)]
            + [_message("gamma", "thumbs_up") for _ in range(4)]
        ),
    )
    svc = SliceService(fake)
    report = await svc.slice_report()

    by_model = {s["model"]: s for s in report["slices"]}
    assert by_model["alpha"]["popularity_bucket"] == "popular"
    assert by_model["beta"]["popularity_bucket"] == "moderate"
    assert by_model["gamma"]["popularity_bucket"] == "long_tail"
    # alpha is popular but its helpful rate (0.5) lags the overall (~0.77) by >10pp.
    assert "high_popularity_low_quality" in by_model["alpha"]["flags"]
    assert report["popularity_flags"][0]["slice"] == "alpha / groq"


@pytest.mark.asyncio
async def test_fairness_report_parity_surfaces():
    fake = FakeSession()
    fake.seed(
        EvaluationLog,
        [
            EvaluationLog(
                trace_id="t1", metric_name="faithfulness", score=0.9, passed=True,
                evaluator="deepeval", reason="grounded", metadata_json={"model": "alpha"},
            ),
            EvaluationLog(
                trace_id="t2", metric_name="faithfulness", score=0.5, passed=False,
                evaluator="deepeval", reason="hallucinated", metadata_json={"model": "alpha"},
            ),
            EvaluationLog(
                trace_id="t3", metric_name="faithfulness", score=0.8, passed=True,
                evaluator="ragas", reason="ok", metadata_json={"model": "beta"},
            ),
        ],
    )
    fake.seed(
        UsageLog,
        [
            _usage("alpha", "groq"),
            _usage("beta", "groq", status="error"),
            _usage("gamma", "unknown_provider", status="error"),
            _usage("delta", "unknown_provider"),
        ],
    )
    svc = SliceService(fake)
    report = await svc.fairness_report()

    eval_parity = {e["group"]: e for e in report["evaluator_parity"]}
    assert eval_parity["deepeval / faithfulness"]["count"] == 2
    assert eval_parity["deepeval / faithfulness"]["pass_rate"] == pytest.approx(0.5)
    assert report["model_parity"][0]["model"] == "alpha"
    assert report["model_parity"][0]["pass_rate"] == pytest.approx(0.5)
    prov = {p["provider"]: p for p in report["provider_error_parity"]}
    assert prov["groq"]["error_rate"] == pytest.approx(0.5)
    assert prov["unknown_provider"]["error_rate"] == pytest.approx(0.5)
    assert prov["unknown_provider"]["flagged"] is True
    assert "fairness" in report["limitations"]
