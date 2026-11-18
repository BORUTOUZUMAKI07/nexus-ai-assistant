"""Guard the Render keepalive against reverting to a one-shot wake check.

The workflow looked correct in review but left both services at 503 in
practice: a cold start takes roughly 50-60s while the old curl gave up after
30s and treated Render's waking-up 503 as a permanent failure. It also
cancelled an in-flight wake every five minutes, exactly when that wake might
have been about to succeed. These are cheap structural checks because a
workflow change cannot be exercised by the unit suite.
"""
from pathlib import Path

WORKFLOW = (
    Path(__file__).resolve().parents[3]
    / ".github"
    / "workflows"
    / "keepalive.yml"
)


def _workflow() -> str:
    assert WORKFLOW.exists(), f"keepalive workflow missing: {WORKFLOW}"
    return WORKFLOW.read_text(encoding="utf-8")


def test_keepalive_runs_often_enough_and_does_not_cancel_a_wake():
    workflow = _workflow()
    assert '- cron: "*/5 * * * *"' in workflow
    assert "cancel-in-progress: false" in workflow
    assert "timeout-minutes: 8" in workflow


def test_both_render_services_are_polled_until_their_expected_status():
    workflow = _workflow()
    assert "name: Wake backend" in workflow
    assert "name: Wake OTLP collector" in workflow
    assert "nexus-backend-owq5.onrender.com/health" in workflow
    assert 'expected="200"' in workflow
    assert "nexus-otel-collector.onrender.com/" in workflow
    assert 'expected="401"' in workflow
    assert "seq 1 12" in workflow
    assert "--max-time 20" in workflow
    assert "sleep 10" in workflow
    assert "--max-time 30" not in workflow
