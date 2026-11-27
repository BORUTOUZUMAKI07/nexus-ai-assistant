"""Production deploys must be gated by green CI and never overlap."""
from __future__ import annotations

import json
from pathlib import Path
from typing import cast

import yaml

ROOT = Path(__file__).resolve().parents[3]
WORKFLOW = ROOT / ".github" / "workflows" / "production-deploy.yml"


def _text() -> str:
    return WORKFLOW.read_text(encoding="utf-8")


def _workflow() -> dict[str, object]:
    # BaseLoader keeps `on:` as the string "on" instead of YAML 1.1's boolean.
    return cast("dict[str, object]", yaml.load(_text(), Loader=yaml.BaseLoader))


def test_production_deploy_waits_for_successful_ci_on_main():
    on = _workflow()["on"]
    assert isinstance(on, dict)
    trigger = on["workflow_run"]
    assert trigger["workflows"] == ["CI"]
    assert trigger["types"] == ["completed"]
    assert trigger["branches"] == ["main"]
    assert "workflow_dispatch" in on


def test_only_success_or_an_operator_dispatch_can_start_a_deploy():
    jobs = _workflow()["jobs"]
    assert isinstance(jobs, dict)
    resolve = jobs["resolve"]["if"]
    deploy = jobs["deploy"]["if"]
    assert "workflow_dispatch" in resolve
    assert "workflow_run.conclusion == 'success'" in resolve
    assert "workflow_run.conclusion == 'success'" in deploy
    assert "always()" not in _text()
    assert "cancelled()" not in _text()


def test_production_deploys_are_serialized_without_cancellation():
    concurrency = _workflow()["concurrency"]
    assert concurrency["group"] == "production-deploy"
    assert concurrency["cancel-in-progress"] == "false"


def test_deploy_uses_only_the_three_named_hook_secrets():
    jobs = _workflow()["jobs"]
    assert isinstance(jobs, dict)
    env = jobs["deploy"]["env"]
    assert env == {
        "RENDER_BACKEND_DEPLOY_HOOK_URL": "${{ secrets.RENDER_BACKEND_DEPLOY_HOOK_URL }}",
        "RENDER_COLLECTOR_DEPLOY_HOOK_URL": "${{ secrets.RENDER_COLLECTOR_DEPLOY_HOOK_URL }}",
        "VERCEL_DEPLOY_HOOK_URL": "${{ secrets.VERCEL_DEPLOY_HOOK_URL }}",
    }


def test_no_hook_url_or_secret_interpolation_is_written_into_a_run_script():
    text = _text()
    assert "https://api.render.com/deploy/" not in text
    assert "https://api.vercel.com/v1/integrations/deploy/" not in text

    jobs = _workflow()["jobs"]
    assert isinstance(jobs, dict)
    for job in jobs.values():
        for step in job["steps"]:
            if "run" in step:
                assert "${{ secrets." not in step["run"], step.get("name")


def test_all_hook_secrets_are_checked_before_any_platform_is_contacted():
    jobs = _workflow()["jobs"]
    assert isinstance(jobs, dict)
    steps = jobs["deploy"]["steps"]
    verify = next(s for s in steps if s.get("name") == "Verify deploy secrets are configured")
    verify_index = steps.index(verify)
    for name in (
        "RENDER_BACKEND_DEPLOY_HOOK_URL",
        "RENDER_COLLECTOR_DEPLOY_HOOK_URL",
        "VERCEL_DEPLOY_HOOK_URL",
    ):
        assert name in verify["run"]
    assert "exit 1" in verify["run"]
    triggers = [i for i, s in enumerate(steps) if str(s.get("name", "")).startswith("Trigger ")]
    assert triggers and verify_index < min(triggers)


def test_render_hooks_are_pinned_to_a_validated_main_sha():
    jobs = _workflow()["jobs"]
    assert isinstance(jobs, dict)
    steps = jobs["deploy"]["steps"]
    validate = next(s for s in steps if s.get("name") == "Validate deploy SHA")
    assert "^[0-9a-f]{40}$" in validate["run"]

    render_steps = [s for s in steps if str(s.get("name", "")).startswith("Trigger Render")]
    assert len(render_steps) == 2
    for step in render_steps:
        script = step["run"]
        assert "?ref=$DEPLOY_SHA" in script
        assert "&ref=$DEPLOY_SHA" in script
        assert 'echo "$hook"' not in script, "a deploy hook URL is a credential"


def test_post_deploy_smoke_runs_only_after_a_successful_deploy():
    jobs = _workflow()["jobs"]
    assert isinstance(jobs, dict)
    smoke = jobs["smoke"]
    assert smoke["needs"] == "deploy"
    assert "SMOKE_BASE_URL" in smoke["env"]
    assert "FRONTEND_SMOKE_URL" in smoke["env"]


def test_post_deploy_smoke_verifies_the_deployed_commit_and_health_endpoints():
    jobs = _workflow()["jobs"]
    assert isinstance(jobs, dict)
    steps = jobs["smoke"]["steps"]
    backend = next(s for s in steps if s.get("name") == "Probe deployed backend health")
    script = backend["run"]
    for endpoint in ("/health", "/health/ready", "/health/dependencies"):
        assert endpoint in script
    assert "--max-time 90" in script and "|| true" in script, "Render needs an ignored warmup"
    assert "for attempt in 1 2 3 4 5" in script
    assert "after 5 attempts" in script
    assert "${DEPLOY_SHA:0:7}" in script, "a healthy old build must not pass the smoke"
    assert "degraded" in script


def test_post_deploy_smoke_probes_the_vercel_bff_health():
    jobs = _workflow()["jobs"]
    assert isinstance(jobs, dict)
    steps = jobs["smoke"]["steps"]
    frontend = next(s for s in steps if s.get("name") == "Probe Vercel frontend BFF health")
    script = frontend["run"]
    assert '"$FRONTEND_SMOKE_URL/api/health"' in script
    assert "for attempt in 1 2 3 4 5" in script
    assert '"200"' in script and "after 5 attempts" in script


def test_vercel_git_auto_deploy_is_disabled_without_blocking_deploy_hooks():
    config = json.loads((ROOT / "frontend" / "vercel.json").read_text(encoding="utf-8"))
    assert config["git"]["deploymentEnabled"] is False
    # The deprecated `github.enabled: false` also blocks deploy hooks.
    assert "github" not in config


def _deployment_guide_section() -> str:
    text = (ROOT / "docs" / "deployment-guide.md").read_text(encoding="utf-8")
    return text.split("## 11. Reference production topology", 1)[1]


def test_deployment_guide_documents_the_ci_gate_and_the_three_secrets():
    section = _deployment_guide_section()
    assert ".github/workflows/production-deploy.yml" in section
    assert "workflow_run" in section
    assert "green ci" in section.lower()
    assert "workflow_dispatch" in section
    for name in (
        "RENDER_BACKEND_DEPLOY_HOOK_URL",
        "RENDER_COLLECTOR_DEPLOY_HOOK_URL",
        "VERCEL_DEPLOY_HOOK_URL",
    ):
        assert name in section


def test_deployment_guide_documents_toggles_smoke_and_rollback():
    section = _deployment_guide_section().lower()
    assert "auto deploy" in section
    assert "deploymentenabled" in section
    assert "github.enabled" in section
    assert "stale" in section
    assert "/health/dependencies" in section
    assert "smoke_base_url" in section
    assert "frontend_smoke_url" in section
    assert "rollback" in section


def test_unrelated_production_guardrails_are_still_intact():
    nightly = (ROOT / ".github" / "workflows" / "nightly.yml").read_text(encoding="utf-8")
    assert '- cron: "0 6 * * *"' in nightly

    chat = (ROOT / "frontend" / "src" / "app" / "api" / "chat" / "route.ts").read_text(encoding="utf-8")
    assert 'Connection: "keep-alive"' in chat

    otel = (ROOT / "docker" / "otel-collector-config.yaml").read_text(encoding="utf-8")
    assert 'inline: "${env:OTLP_AUTH_USER}:${env:OTLP_AUTH_PASSWORD}"' in otel

    release = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
    assert 'tags:\n      - "v*"' in release
