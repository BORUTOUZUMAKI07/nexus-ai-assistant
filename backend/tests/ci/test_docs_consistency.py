"""Documentation must not contradict the shipped manifest or deployment target.

Two drifts were found by the audit: prose still claimed Next.js 16.4.2 while the
manifest declares 16.4.6, and one section called the Docker image the
"recommended deployment" even though production runs the frontend on Vercel.
"""
import json
import re
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[3]


def test_documented_nextjs_version_matches_the_manifest():
    manifest = json.loads((_ROOT / "frontend" / "package.json").read_text(encoding="utf-8"))
    declared = manifest["dependencies"]["next"].lstrip("^")
    lock = json.loads((_ROOT / "frontend" / "package-lock.json").read_text(encoding="utf-8"))
    locked = lock["packages"][""]["dependencies"]["next"].lstrip("^")
    assert declared == locked, "manifest and lockfile disagree about the Next.js version"

    for relative in ("docs/deployment-guide.md", "docs/user-guide.md", "frontend/README.md"):
        path = _ROOT / relative
        stale = set(re.findall(r"16\.4\.\d+", path.read_text(encoding="utf-8"))) - {declared}
        assert not stale, f"{relative} still claims {sorted(stale)}; manifest says {declared}"


def test_deployment_guide_names_vercel_as_the_reference_frontend_target():
    """RED: the frontend section must not recommend the self-host image instead."""
    text = (_ROOT / "docs" / "deployment-guide.md").read_text(encoding="utf-8")
    section = text.split("## 5. Frontend", 1)[1].split("## 6.", 1)[0]

    assert "Vercel" in section
    assert "BACKEND_URL" in section
    assert "recommended deployment is the provided `frontend/Dockerfile`" not in section


def test_topology_table_still_places_the_frontend_on_vercel():
    text = (_ROOT / "docs" / "deployment-guide.md").read_text(encoding="utf-8")
    assert "| Frontend | **Vercel** |" in text
    assert (_ROOT / "frontend" / "vercel.json").is_file()


def _m5_finding() -> str:
    """The M5 paragraph, isolated from the rest of the severity-ranked findings."""
    text = (_ROOT / "docs" / "feature-service-audit.md").read_text(encoding="utf-8")
    return text.split("**M5 —", 1)[1].split("**M6 —", 1)[0]


def test_m5_doc_no_longer_claims_the_stream_path_has_no_webhooks_or_cache_work():
    """RED: the finding was fixed in code, but its prose still said the primary
    chat path silently skipped webhooks and cache bookkeeping entirely.

    `execute_run` now enqueues both `message.completed` and semantic-cache
    invalidation as durable outbox rows after the reply commits (fail-open
    fallback included), and live-verification D7 records a real delivery. The
    doc must say that, and must name the tests that guard it, instead of leaving
    an open finding that no longer exists.
    """
    finding = _m5_finding()

    assert "outbox" in finding
    assert "message.completed" in finding
    assert "test_post_answer_outbox_executor.py" in finding
    assert "test_post_answer_outbox.py" in finding
    assert "has none of these" not in finding
    assert "effectively never fire" not in finding


def test_m5_doc_explains_the_deliberate_remaining_asymmetry():
    """Cache lookup stays sync-only (a cached blob cannot replace an agent SSE
    run), and no canary/A/B rollout is deployed — so adding variant selection to
    the stream path would be a new rollout, not a parity fix. The doc must say
    both, or a reader will re-open M5 and reintroduce exactly that rollout.
    """
    finding = _m5_finding()
    lowered = finding.lower()

    assert "by design" in lowered
    assert "no canary/a/b" in lowered or "not deployed" in lowered
    assert "invalidate" in lowered


def test_m5_remediation_item_no_longer_asks_for_a_full_path_unification():
    text = (_ROOT / "docs" / "feature-service-audit.md").read_text(encoding="utf-8")
    remediation = text.split("## Recommended remediation order", 1)[1]
    item_5 = next(line for line in remediation.splitlines() if line.startswith("5. **M5**"))

    assert "unify sync/stream chat paths" not in item_5
    assert "resolved" in item_5.lower()


def test_observability_docs_do_not_present_a_prometheus_scrape_as_deployed():
    """The deployed Render stack pushes OTLP; no service scrapes `/metrics`.

    `NexusRequestsDown` is an `up == 0` alert, so it can never fire in
    production. Docs that still lead with scrape triage send an on-call engineer
    to a signal that does not exist while the real outage is a silent OTLP
    pipeline.
    """
    def flat(relative: str) -> str:
        # Prose is wrapped at 80 columns; phrase checks must not depend on a
        # line break landing in the middle of the term. Strip Markdown
        # blockquotes and YAML comment markers first: they are syntax, not prose.
        text = (_ROOT / relative).read_text(encoding="utf-8")
        text = re.sub(r"(?m)^\s*>\s?", "", text)
        text = re.sub(r"(?m)^\s*#\s?", "", text)
        return " ".join(text.split())

    slo = flat("docs/slo.md")
    rules = flat("docs/alerting/alert-rules.yml")
    runbooks = flat("docs/runbooks/README.md")
    outage = flat("docs/runbooks/full-outage.md")
    api = flat("docs/api-reference.md")

    assert "no Prometheus scraper" in slo
    assert "`NexusRequestsDown` only applies" in slo
    assert "contract only — no instrumentation is implemented" not in slo

    assert "`NexusRequestsDown` only applies" in rules
    assert "local-only" in rules.lower()

    assert "`NexusRequestsDown` only applies" in runbooks
    assert "signal loss" in runbooks.lower()

    assert "no Prometheus scraper" in outage
    assert "Read `/metrics` scrape errors first" not in outage

    assert "local/debug only" in api
