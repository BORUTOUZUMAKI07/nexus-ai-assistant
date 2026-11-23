"""The nightly deployed smoke check is a guardrail: keep the cron and the probe."""

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[3]
NIGHTLY = ROOT / ".github" / "workflows" / "nightly.yml"


def _text() -> str:
    return NIGHTLY.read_text(encoding="utf-8")


def test_nightly_cron_is_unchanged():
    text = _text()
    assert text.count("cron:") == 1, "nightly.yml must keep exactly one schedule"
    assert '- cron: "0 6 * * *"' in text


def test_nightly_smokes_the_deployed_dependency_endpoint():
    text = _text()
    assert "Smoke check deployed dependency health" in text
    assert "/health/dependencies" in text
    assert "/health/ready" in text
    assert "SMOKE_BASE_URL" in text
    assert "attempt" in text, "a cold start must be retried before failing"
    assert "--max-time 60" in text, "slow-but-healthy probes must not fail the transport"


def test_nightly_smoke_warms_the_deployed_host_before_the_strict_probe():
    lines = _text().splitlines()
    warmup = [i for i, line in enumerate(lines) if "--max-time 90" in line and "curl" in line]
    strict = [i for i, line in enumerate(lines) if "--max-time 60" in line and "curl" in line]
    assert warmup, "a cold Render start needs an ignored warmup request first"
    assert "|| true" in lines[warmup[0]], "warmup failure must never fail the step"
    assert strict and min(warmup) < min(strict), "warmup must run before the strict probes"


def test_nightly_smoke_retries_long_enough_for_a_cold_start():
    text = _text()
    assert "for attempt in 1 2 3 4 5" in text
    assert "after 5 attempts" in text


def test_nightly_smoke_does_not_interpolate_secrets():
    assert "${{ secrets." not in _text()


def test_no_step_runs_in_the_project_directory_before_checkout():
    workflow = yaml.safe_load(_text())
    for name, job in workflow["jobs"].items():
        default_cwd = job.get("defaults", {}).get("run", {}).get("working-directory")
        if not default_cwd:
            continue
        steps = job["steps"]
        checkout_index = next(
            i for i, step in enumerate(steps) if str(step.get("uses", "")).startswith("actions/checkout@")
        )
        for step in steps[:checkout_index]:
            if "run" not in step:
                continue
            cwd = step.get("working-directory", default_cwd)
            assert cwd in {".", "${{ github.workspace }}"}, (
                f"{name}: step {step.get('name')!r} runs in {cwd!r} before checkout"
            )
