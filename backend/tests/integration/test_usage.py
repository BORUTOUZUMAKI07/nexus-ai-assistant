"""Usage & telemetry endpoints integration tests."""
import uuid

import pytest
from backend.app.domain.usage.repository import UsageRepository
from backend.app.domain.usage.schemas import EvaluationLogCreate, UsageLogCreate
from backend.app.services.observability.cost_tracking import cost_tracking_service
from backend.app.services.org_service import OrganizationService


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

    conv_resp = await client.post(
        "/api/v1/conversations",
        headers=user_auth_headers,
        json={"title": "Eval conversation", "mode": "normal"},
    )
    assert conv_resp.status_code == 201
    conv_id = uuid.UUID(conv_resp.json()["id"])

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
    # System-level (conversation-agnostic) evaluations and the owner's
    # conversation-scoped evaluation are both returned.
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


@pytest.mark.asyncio
async def test_org_usage_summary_rolls_up_per_org(client, user_auth_headers, db_session, test_user):
    """Per-org usage/cost rollup (T-07): the member can read their org's
    aggregate, and the cost leg comes from the CostLog rollup."""
    org = await OrganizationService(db_session).create(test_user.id, "Acme Analytics")
    org_id = org.id

    repo = UsageRepository(db_session)
    await repo.log_usage(
        UsageLogCreate(
            user_id=test_user.id,
            org_id=org_id,
            model="llama-3.3-70b-versatile",
            prompt_tokens=100,
            completion_tokens=50,
            cost_usd=0.0015,
        )
    )
    await cost_tracking_service.record_cost_log(
        session=db_session,
        user_id=test_user.id,
        org_id=org_id,
        model="llama-3.3-70b-versatile",
        provider="groq",
        prompt_tokens=100,
        completion_tokens=50,
    )

    resp = await client.get(f"/api/v1/orgs/{org_id}/usage-summary", headers=user_auth_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["organization_id"] == str(org_id)
    assert body["total_requests"] == 1
    assert body["prompt_tokens"] == 100
    assert body["completion_tokens"] == 50
    assert body["total_tokens"] == 150
    assert body["usage_cost_usd"] > 0
    assert body["cost_entries"] == 1
    assert body["cost_total_usd"] > 0


@pytest.mark.asyncio
async def test_org_usage_summary_hidden_from_non_members(client, user_auth_headers):
    """Non-members get 404 (org existence kept private), never a summary."""
    resp = await client.get(
        f"/api/v1/orgs/{uuid.uuid4()}/usage-summary", headers=user_auth_headers
    )
    assert resp.status_code == 404
