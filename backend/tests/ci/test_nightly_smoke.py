"""The nightly deployed smoke check is a guardrail: keep the cron and the probe."""

from pathlib import Path

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


def test_nightly_smoke_does_not_interpolate_secrets():
    assert "${{ secrets." not in _text()
