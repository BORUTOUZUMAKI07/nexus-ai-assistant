"""
Unit tests for the responsible-ML / compliance audit surface.
"""
import uuid

import pytest
from backend.app.domain.hook.models import HookPolicy
from backend.app.domain.prompt.models import PromptVersion
from backend.app.domain.redteam.models import RedTeamRun
from backend.app.domain.system.models import AuditLog
from backend.app.domain.usage.models import EvaluationLog, UsageLog
from backend.app.services.audit_service import (
    AuditService,
    eu_ai_act_classification,
    retention_statement,
)
from backend.tests.fakes import FakeSession


def test_eu_ai_act_classification_statement():
    cls = eu_ai_act_classification()
    assert cls["classification"] == "downstream provider of a general-purpose chatbot"
    assert "art_50" in cls["transparency_obligations"]
    assert "2025-08-02" in cls["gpaI_models"]["upstream_obligations"]


def test_retention_statement_mentions_cache():
    statement = retention_statement()
    assert "Response cache TTL" in statement


@pytest.mark.asyncio
async def test_report_empty_database_still_sane():
    report = await AuditService(FakeSession()).report()
    assert report["controls"]["pii_redaction_enabled"] is False
    assert report["red_team"]["run_count"] == 0
    assert isinstance(report["eu_ai_act"], dict)
    assert report["gdpr"]["gdpr_export"] == 0
    assert report["gdpr"]["gdpr_erasure"] == 0


@pytest.mark.asyncio
async def test_report_aggregates_evidence_sections():
    fake = FakeSession()
    user_id = uuid.uuid4()
    fake.seed(
        HookPolicy,
        [HookPolicy(name="block shell", tool_name="run_shell", action="block", enabled=True)],
    )
    fake.seed(
        UsageLog,
        [
            UsageLog(user_id=user_id, model="alpha", provider="groq", metadata_json={"prompt_variant": "canary_v1"}),
            UsageLog(user_id=user_id, model="beta", provider="openai"),
        ],
    )
    fake.seed(
        PromptVersion,
        [
            PromptVersion(
                template_id=uuid.uuid4(), version=1, system_prompt="sys", created_by=user_id,
            ),
        ],
    )
    fake.seed(
        RedTeamRun,
        [RedTeamRun(total_probes=4, blocked_probes=3, defense_rate=0.75, report={"results": []})],
    )
    fake.seed(
        AuditLog,
        [
            AuditLog(user_id=user_id, action="gdpr_export", resource_type="user"),
            AuditLog(user_id=user_id, action="gdpr_erasure", resource_type="user"),
            AuditLog(user_id=user_id, action="tool_execution", resource_type="tool"),
        ],
    )
    fake.seed(
        EvaluationLog,
        [
            EvaluationLog(trace_id="t1", metric_name="faithfulness", score=0.9, passed=True, evaluator="deepeval"),
            EvaluationLog(trace_id="t2", metric_name="faithfulness", score=0.4, passed=False, evaluator="deepeval"),
        ],
    )

    report = await AuditService(fake).report()

    assert report["lifecycle_hooks"]["policy_count"] == 1
    assert report["model_provenance"][0]["model"] == "alpha"
    assert report["model_provenance"][0]["experiment_variants_seen"] == ["canary_v1"]
    assert report["prompt_provenance"]["version_count"] == 1
    assert report["red_team"]["defense_rate"] == 0.75
    assert report["gdpr"]["gdpr_export"] == 1
    assert report["gdpr"]["gdpr_erasure"] == 1
    assert report["evaluations"][0]["pass_rate"] == 0.5


@pytest.mark.asyncio
async def test_redteam_runs_lists_latest_runs():
    fake = FakeSession()
    fake.seed(
        RedTeamRun,
        [
            RedTeamRun(total_probes=4, blocked_probes=2, defense_rate=0.5, report={"results": [{"p": 1}]}),
            RedTeamRun(total_probes=4, blocked_probes=4, defense_rate=1.0, report={"results": []}),
        ],
    )
    runs = await AuditService(fake).redteam_runs(limit=5)
    assert len(runs) == 2
    assert {r["defense_rate"] for r in runs} == {0.5, 1.0}
    assert runs[0]["probe_count"] == 1
