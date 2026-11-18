"""Guard the Render keepalive against an unsafe or unattributable wake strategy.

Two incidents shaped this file:

1. The original one-shot curl treated Render's 503 "waking up" response as a
   hard failure, and `cancel-in-progress: true` killed a wake exactly when it
   was about to succeed. Bounded polling and `cancel-in-progress: false` fixed
   that; those assertions are kept verbatim below.
2. The locked product decision is that **exactly one** Free service is kept
   warm: the user-facing backend (the BFF times out after ~55s on a cold start).
   The collector is woken only on demand, because two always-on Free services
   need ~1,460 of the workspace's 750 monthly Free instance hours and Render
   then suspends every Free service until the next month (render.yaml:150-157,
   docs/deployment-guide.md:293-301). GitHub cron is the primary scheduler; an
   external backstop is independent and targets the *same single service*.

These are cheap structural checks because a workflow change cannot be
exercised by the unit suite.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "keepalive.yml"
BACKSTOP = REPO_ROOT / "scripts" / "keepalive-backstop.sh"
SCHEDULE_RUNBOOK = REPO_ROOT / "docs" / "runbooks" / "scheduled-runs-not-firing.md"

# One always-on Free web service is ~730 h/month (24 x ~30.4 days). Two are
# ~1,460 h, which exceeds the 750 h workspace budget. Sources:
# render.yaml:150-157 and docs/deployment-guide.md:293-301.
HOURS_PER_ALWAYS_ON_FREE_SERVICE = 730
FREE_INSTANCE_HOUR_BUDGET = 750


def _workflow() -> str:
    assert WORKFLOW.exists(), f"keepalive workflow missing: {WORKFLOW}"
    return WORKFLOW.read_text(encoding="utf-8")


def _backstop() -> str:
    assert BACKSTOP.exists(), f"external wake backstop missing: {BACKSTOP}"
    return BACKSTOP.read_text(encoding="utf-8")


def _parsed() -> dict:
    return yaml.safe_load(_workflow())


def _on_block(data: dict) -> dict:
    """The `on:` block. PyYAML (YAML 1.1) resolves the bare key `on` to True."""
    block = data.get("on")
    if block is None:
        block = data.get(True)
    assert isinstance(block, dict), "the workflow has no `on:` block"
    return block


def _scheduled_jobs(data: dict) -> set[str]:
    """Job names that actually run when GitHub fires the cron."""
    assert "schedule" in _on_block(data), "the 5-minute cron was removed"
    return {
        name
        for name, job in (data.get("jobs") or {}).items()
        if "inputs.wake_collector" not in str(job.get("if", ""))
    }


def _backstop_targets() -> set[str]:
    """Service labels probed on every backstop run; comment lines excluded."""
    targets: set[str] = set()
    for raw in _backstop().splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        match = re.match(r'^probe\s+(backend|collector)\s+"\$', line)
        if match:
            targets.add(match.group(1))
    return targets


# ─── original invariants (kept; do not delete) ────────────────────────────────


def test_keepalive_runs_often_enough_and_does_not_cancel_a_wake():
    workflow = _workflow()
    assert '- cron: "*/5 * * * *"' in workflow
    assert "cancel-in-progress: false" in workflow
    assert "timeout-minutes: 8" in workflow


def test_both_render_services_are_polled_until_their_expected_status():
    """Both probes stay in the workflow; the collector's is manual/incident-only.

    Keeping the collector probe available is the point — it must be woken on
    demand when telemetry is being investigated. What changed is *when* it
    runs, asserted separately in test_collector_is_manual_only_not_on_cron.
    """
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


# ─── bounds, concurrency and attribution ──────────────────────────────────────


def test_every_job_has_an_explicit_timeout_and_the_stable_concurrency_group():
    """One timeout on one job is not a timeout on both.

    A job that loses its own bound can hang until GitHub's 360-minute default.
    """
    workflow = _workflow()
    assert "\n  backend:\n" in workflow, "the backend keepalive job was removed"
    assert "\n  collector:\n" in workflow, "the collector probe job was removed"
    assert workflow.count("timeout-minutes: 8") >= 2, (
        "both jobs need their own explicit timeout-minutes"
    )
    assert "group: render-keepalive" in workflow, (
        "the concurrency group serialises the heartbeat streams so a stale wake "
        "cannot overlap a fresh one"
    )
    assert "cancel-in-progress: false" in workflow


def test_runs_record_entry_point_and_actual_trigger_without_gating_on_the_scheduler():
    """Attribute every run, but never treat GitHub's scheduler as authoritative.

    Live observation: `event=schedule` returned `total_count=0` while manual
    dispatch succeeded. Logging `${{ github.event_name }}` records what actually
    started a run (schedule vs workflow_dispatch) for triage; using it as an
    `if:` condition would silently skip the heartbeat whenever the scheduler
    misreports, which is the exact failure this guards against.
    """
    workflow = _workflow()
    assert workflow.count("entry_point: github_actions") >= 2, (
        "both jobs must log entry_point: github_actions"
    )
    assert workflow.count("trigger_event: ${{ github.event_name }}") >= 2, (
        "both jobs must log the actual trigger event (schedule vs workflow_dispatch)"
    )
    assert workflow.count("X-Nexus-Keepalive-Probe: github_actions") >= 2, (
        "both probes must send the HTTP-layer entry-point header so the "
        "receiver's own logs can distinguish GitHub traffic from the backstop"
    )
    assert "github.event_name ==" not in workflow, (
        "do not gate on github.event_name: the GitHub scheduler was observed to "
        "report zero schedule events while manual dispatch still worked"
    )


# ─── locked single-warmed-service decision ────────────────────────────────────


def test_exactly_one_service_is_warmed_by_the_cron():
    scheduled = _scheduled_jobs(_parsed())
    assert scheduled == {"backend"}, (
        f"the 5-minute cron must warm exactly one Free service (the user-facing "
        f"backend); got {sorted(scheduled)}"
    )


def test_monthly_always_on_hours_stay_under_the_free_budget():
    hours = len(_scheduled_jobs(_parsed())) * HOURS_PER_ALWAYS_ON_FREE_SERVICE
    assert 2 * HOURS_PER_ALWAYS_ON_FREE_SERVICE > FREE_INSTANCE_HOUR_BUDGET, (
        "the guard is meaningless if two always-on services fit the budget"
    )
    assert hours < FREE_INSTANCE_HOUR_BUDGET, (
        f"{hours} always-on Free instance hours/month exceeds the "
        f"{FREE_INSTANCE_HOUR_BUDGET} h budget; Render suspends every Free "
        "service until the next month"
    )


def test_collector_is_manual_only_not_on_cron():
    data = _parsed()
    on = _on_block(data)
    assert "workflow_dispatch" in on, "manual dispatch is required to wake the collector"
    dispatch = on["workflow_dispatch"] or {}
    inputs = dispatch.get("inputs") or {}
    assert "wake_collector" in inputs, "the manual wake input was removed"
    collector = (data.get("jobs") or {}).get("collector") or {}
    assert "inputs.wake_collector" in str(collector.get("if", "")), (
        "the collector job must be gated behind the manual input"
    )
    assert "collector" not in _scheduled_jobs(data), "the collector is back on the cron"


# ─── independent external wake backstop ───────────────────────────────────────


def test_external_backstop_exists_and_labels_its_entry_point():
    text = _backstop()
    assert "entry_point: external_backstop" in text, (
        "the backstop must identify itself so a wake seen in receiver logs can "
        "be attributed to it"
    )
    assert "trigger_event: external_scheduler" in text
    assert "X-Nexus-Keepalive-Probe: external_backstop" in text


def test_external_backstop_is_authenticated_only_by_a_caller_supplied_header():
    """No secret in the file: the caller supplies one optional header at runtime.

    The header is an environment variable with an EMPTY default, so a scheduler
    that does not set it still gets a working (unauthenticated) probe, and a
    scheduler that protects /health can pass its own token without committing it.
    The value is never echoed — only status codes are logged.
    """
    text = _backstop()
    assert 'KEEPALIVE_HEADER="${KEEPALIVE_HEADER:-}"' in text, (
        "the optional auth header must come from the environment with an empty "
        "default; embedding a value here would commit a secret"
    )
    assert '-H "$KEEPALIVE_HEADER"' in text, (
        "the caller-supplied header must actually be attached to the curl probe"
    )
    assert 'echo "$KEEPALIVE_HEADER"' not in text
    assert 'echo "${KEEPALIVE_HEADER}"' not in text, (
        "never log the header value: it may carry a bearer token"
    )


def test_external_backstop_probes_only_the_warmed_backend_with_bounded_polling():
    text = _backstop()
    assert "https://nexus-backend-owq5.onrender.com/health" in text
    assert 'probe backend "$BACKEND_URL" "200"' in text
    assert 'KEEPALIVE_MAX_ATTEMPTS:-12' in text, "cold-start polling must be bounded"
    assert 'KEEPALIVE_REQUEST_TIMEOUT:-20' in text
    assert 'KEEPALIVE_POLL_INTERVAL:-10' in text
    assert 'seq 1 "$MAX_ATTEMPTS"' in text
    assert '--max-time "$REQUEST_TIMEOUT"' in text
    assert 'sleep "$POLL_INTERVAL"' in text
    assert _backstop_targets() == {"backend"}, (
        "the backstop must never probe the collector: recurring wakes of two "
        "Free services exceed the 750 h/month budget"
    )


def test_backstop_targets_are_a_subset_of_the_scheduled_targets():
    targets = _backstop_targets()
    assert targets, "the backstop probes nothing"
    scheduled = _scheduled_jobs(_parsed())
    assert targets <= scheduled, (
        f"backstop targets {sorted(targets)} are not a subset of the cron's "
        f"scheduled targets {sorted(scheduled)}"
    )


def test_external_backstop_setup_is_documented_for_real_schedulers():
    assert SCHEDULE_RUNBOOK.exists(), f"scheduling runbook missing: {SCHEDULE_RUNBOOK}"
    runbook = SCHEDULE_RUNBOOK.read_text(encoding="utf-8")
    assert "keepalive-backstop.sh" in runbook
    assert "cron-job.org" in runbook.lower()
    assert "UptimeRobot" in runbook
    assert "event=schedule" in runbook
    assert "750" in runbook
