"""Usage & telemetry endpoints integration tests."""
import uuid

import pytest
from backend.app.domain.usage.repository import UsageRepository
from backend.app.domain.usage.schemas import EvaluationLogCreate, UsageLogCreate


@pytest.mark.asyncio
async def test_summary_empty_for_fresh_user(client, user_auth_headers):
    resp = await client.get("/api/v1/usage/summary", headers=user_auth_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["total_requests"] == 0
    assert body["total_tokens"] == 0
    assert body["prompt_tokens"] == 0
    assert body["completion_tokens"] == 0


@pytest.mark.asyncio
async def test_summary_aggregates_usage_logs(client, user_auth_headers, db_session, test_user):
    repo = UsageRepository(db_session)
    await repo.log_usage(
        UsageLogCreate(
            user_id=test_user.id,
            model="llama-3.3-70b-versatile",
            prompt_tokens=100,
            completion_tokens=50,
            latency_ms=200.5,
        )
    )
    await repo.log_usage(
        UsageLogCreate(
            user_id=test_user.id,
            model="llama-3.3-70b-versatile",
            prompt_tokens=300,
            completion_tokens=150,
            cached_tokens=80,
            latency_ms=500.0,
        )
    )

    resp = await client.get("/api/v1/usage/summary", headers=user_auth_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["total_requests"] == 2
    assert body["prompt_tokens"] == 400
    assert body["completion_tokens"] == 200
    assert body["total_tokens"] == 600
    assert body["cached_tokens"] == 80


@pytest.mark.asyncio
async def test_evaluations_round_trip(client, user_auth_headers, db_session, test_user):
    repo = UsageRepository(db_session)
    conv_id = uuid.uuid4()
    await repo.log_evaluation(
        EvaluationLogCreate(
            trace_id="trace-1",
            conversation_id=conv_id,
            metric_name="faithfulness",
            score=0.85,
            passed=True,
            evaluator="deepeval",
        )
    )
    await repo.log_evaluation(
        EvaluationLogCreate(
            trace_id="trace-2",
            metric_name="other_metric",
            score=0.40,
            passed=False,
            reason="drift",
            evaluator="offline",
        )
    )

    all_evals = await client.get("/api/v1/usage/evaluations", headers=user_auth_headers)
    assert all_evals.status_code == 200
    metrics = {e["metric_name"] for e in all_evals.json()}
    assert {"faithfulness", "other_metric"} <= metrics

    filtered = await client.get(
        f"/api/v1/usage/evaluations?conversation_id={conv_id}",
        headers=user_auth_headers,
    )
    assert filtered.status_code == 200
    assert len(filtered.json()) == 1
    assert filtered.json()[0]["metric_name"] == "faithfulness"


@pytest.mark.asyncio
async def test_evaluations_requires_auth(client):
    resp = await client.get("/api/v1/usage/summary")
    assert resp.status_code == 401
