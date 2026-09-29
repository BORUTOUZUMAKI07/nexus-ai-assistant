"""
Observability Viewer (admin dashboards, zero external dependencies).

Closes the "no trace/cost/drift viewer" gap: a self-contained HTML dashboard
served by the admin API that renders the observability stack status, cost
rollups, usage telemetry, the drift report and the evaluation scorecard —
straight from the local tables (usage_logs, cost_logs, evaluation_logs) with no
JS framework, no CDN and no new dependencies.

Every query here uses plain ``select(Model)`` statements so the data gatherer
is exercisable against the shared in-memory FakeSession in unit tests.
"""
from __future__ import annotations

import html
import importlib.util
from datetime import UTC, datetime
from typing import Any

import structlog
from backend.app.core.config import settings
from backend.app.domain.usage.models import CostLog, EvaluationLog, UsageLog
from backend.app.services.monitoring.drift_service import DriftService
from backend.app.services.observability.metrics import metrics_collector
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

logger = structlog.get_logger(__name__)

_RECENT_ROWS = 25


# ─────────────────────────────────────────────────────────────────────────── #
# Stack status (shared by /monitoring/observability and the viewer)
# ─────────────────────────────────────────────────────────────────────────── #


def observability_stack_status() -> dict[str, Any]:
    """Which collectors are wired and live (honest: enabled-but-uninstalled New Relic is reported)."""
    newrelic_installed = importlib.util.find_spec("opentelemetry") is not None
    newrelic_configured = bool((settings.NEW_RELIC_OTLP_ENDPOINT or "").strip())
    return {
        "newrelic": {
            "enabled": bool(settings.NEW_RELIC_ENABLED),
            "configured": newrelic_configured,
            "package_installed": newrelic_installed,
            "status": (
                "live"
                if settings.NEW_RELIC_ENABLED and newrelic_installed and newrelic_configured
                else "inactive"
            ),
            "activate": "NEW_RELIC_ENABLED=true NEW_RELIC_OTLP_ENDPOINT=http://localhost:4318 "
                        "(run the Layer-2 collector from docker-compose — it owns the New Relic ingest key)",
        },
        "drift_monitoring": {"enabled": True, "endpoint": "/api/v1/admin/monitoring/drift"},
        "audit_logs": True,
        "pii_redaction": {"enabled": bool(settings.PII_REDACTION_ENABLED)},
        "metrics_collector": True,
    }


# ─────────────────────────────────────────────────────────────────────────── #
# Data gathering
# ─────────────────────────────────────────────────────────────────────────── #


def _percentile(sorted_samples: list[float], pct: float) -> float:
    if not sorted_samples:
        return 0.0
    idx = min(len(sorted_samples) - 1, int(len(sorted_samples) * pct))
    return round(sorted_samples[idx], 2)


def _latency_stats(rows: list[UsageLog]) -> dict[str, float | int] | None:
    samples = sorted(float(r.latency_ms or 0.0) for r in rows)
    if not samples:
        return None
    n = len(samples)
    avg = sum(samples) / n
    return {
        "count": n,
        "avg_ms": round(avg, 2),
        "p50_ms": _percentile(samples, 0.50),
        "p95_ms": _percentile(samples, 0.95),
        "p99_ms": _percentile(samples, 0.99),
    }


def _aggregate_by(rows: list[Any], key_attr: str, value_fn) -> list[dict[str, Any]]:
    buckets: dict[Any, list[Any]] = {}
    for row in rows:
        buckets.setdefault(getattr(row, key_attr), []).append(row)
    return sorted(
        ({"key": key, "rows": rows} for key, rows in buckets.items()),
        key=lambda b: value_fn(b["rows"]),
        reverse=True,
    )


async def gather_viewer_data(session: AsyncSession) -> dict[str, Any]:
    """Assemble every section the dashboard renders (cost, usage, drift, evals)."""
    usage_rows = (await session.exec(select(UsageLog))).all() or []
    cost_rows = (await session.exec(select(CostLog))).all() or []
    eval_rows = (await session.exec(select(EvaluationLog))).all() or []

    # ── cost rollups (from the monthly cost_logs) ────────────────────────────
    total_cost = sum(float(r.total_cost or 0.0) for r in cost_rows)
    by_model = []
    for bucket in _aggregate_by(cost_rows, "model", lambda rs: sum(float(r.total_cost or 0.0) for r in rs)):
        rows = bucket["rows"]
        by_model.append(
            {
                "model": bucket["key"],
                "calls": len(rows),
                "input_cost": round(sum(float(r.input_cost or 0.0) for r in rows), 6),
                "output_cost": round(sum(float(r.output_cost or 0.0) for r in rows), 6),
                "total_cost": round(sum(float(r.total_cost or 0.0) for r in rows), 6),
            }
        )
    by_period = []
    for bucket in _aggregate_by(cost_rows, "billing_period", lambda rs: sum(float(r.total_cost or 0.0) for r in rs)):
        rows = bucket["rows"]
        by_period.append(
            {
                "period": bucket["key"],
                "calls": len(rows),
                "total_cost": round(sum(float(r.total_cost or 0.0) for r in rows), 6),
            }
        )

    # ── usage telemetry (from usage_logs) ────────────────────────────────────
    errors = [r for r in usage_rows if r.status not in (None, "success")]
    usage_by_model = []
    for bucket in _aggregate_by(usage_rows, "model", lambda rs: sum(int(r.total_tokens or 0) for r in rs)):
        rows = bucket["rows"]
        usage_by_model.append(
            {
                "model": bucket["key"],
                "requests": len(rows),
                "tokens": sum(int(r.total_tokens or 0) for r in rows),
                "cost_usd": round(sum(float(r.cost_usd or 0.0) for r in rows), 6),
            }
        )
    recent_usage = sorted(usage_rows, key=lambda r: r.created_at or datetime.min, reverse=True)[:_RECENT_ROWS]
    recent_usage_rows = [
        {
            "time": _iso(r.created_at),
            "model": r.model,
            "provider": r.provider,
            "tokens": int(r.total_tokens or 0),
            "cost_usd": round(float(r.cost_usd or 0.0), 6),
            "latency_ms": float(r.latency_ms or 0.0),
            "status": r.status,
            "user_id": str(r.user_id) if r.user_id else None,
            "conversation_id": str(r.conversation_id) if r.conversation_id else None,
        }
        for r in recent_usage
    ]

    # ── evaluations scorecard (from evaluation_logs) ─────────────────────────
    eval_by_metric = []
    for bucket in _aggregate_by(eval_rows, "metric_name", lambda rs: sum(1 for r in rs)):
        rows = bucket["rows"]
        passed = sum(1 for r in rows if r.passed)
        eval_by_metric.append(
            {
                "metric": bucket["key"],
                "count": len(rows),
                "passed": passed,
                "pass_rate": round(passed / len(rows), 3) if rows else 0.0,
            }
        )
    recent_evals = sorted(eval_rows, key=lambda r: r.created_at or datetime.min, reverse=True)[:_RECENT_ROWS]
    recent_eval_rows = [
        {
            "time": _iso(r.created_at),
            "metric": r.metric_name,
            "score": r.score,
            "passed": bool(r.passed),
            "reason": r.reason,
            "evaluator": r.evaluator,
        }
        for r in recent_evals
    ]

    # ── drift report (graceful when a window has no samples) ─────────────────
    drift: dict[str, Any] = {}
    try:
        drift = await DriftService(session).drift_report()
    except Exception as exc:
        logger.warning("viewer_drift_query_failed", error=str(exc))
        drift = {"detected": False, "drifting_metrics": 0, "error": f"Drift report unavailable: {exc}"}

    return {
        "as_of": datetime.now(UTC).replace(tzinfo=None).isoformat(timespec="seconds"),
        "stack": observability_stack_status(),
        "cost": {
            "total_usd": round(total_cost, 6),
            "log_count": len(cost_rows),
            "by_model": by_model,
            "by_period": by_period,
        },
        "usage": {
            "request_count": len(usage_rows),
            "error_count": len(errors),
            "error_rate": round(len(errors) / len(usage_rows), 4) if usage_rows else 0.0,
            "latency": _latency_stats(usage_rows),
            "by_model": usage_by_model,
            "recent": recent_usage_rows,
        },
        "evaluations": {
            "log_count": len(eval_rows),
            "by_metric": eval_by_metric,
            "recent": recent_eval_rows,
        },
        "drift": drift,
        "telemetry": metrics_collector.get_summary(),
    }


def _iso(value: datetime | None) -> str | None:
    return value.isoformat(timespec="seconds") if value else None


# ─────────────────────────────────────────────────────────────────────────── #
# HTML rendering (self-contained — inline CSS only, no CDN, no JS framework)
# ─────────────────────────────────────────────────────────────────────────── #


def _esc(value: Any) -> str:
    return html.escape("" if value is None else str(value))


def _bar(value: float, max_value: float) -> str:
    if max_value <= 0:
        return "0%"
    return f"{max(2.0, 100.0 * value / max_value):.1f}%"


def _cost_model_rows(data: dict[str, Any]) -> str:
    rows = data["cost"]["by_model"]
    if not rows:
        return '<tr><td colspan="6" class="muted">No cost logs yet — spend appears once the LLM is used.</td></tr>'
    max_cost = max(r["total_cost"] for r in rows) or 1.0
    cells = []
    for r in rows:
        cells.append(
            "<tr>"
            f"<td>{_esc(r['model'])}</td>"
            f"<td>{r['calls']}</td>"
            f"<td>${r['total_cost']:.6f}</td>"
            f"<td>${r['input_cost']:.6f}</td>"
            f"<td>${r['output_cost']:.6f}</td>"
            f'<td><div class="bar"><div class="fill" style="width:{_bar(r["total_cost"], max_cost)}"></div></div></td>'
            "</tr>"
        )
    return "".join(cells)


def _cost_period_rows(data: dict[str, Any]) -> str:
    rows = data["cost"]["by_period"]
    if not rows:
        return '<tr><td colspan="3" class="muted">No billing periods recorded.</td></tr>'
    max_cost = max(r["total_cost"] for r in rows) or 1.0
    cells = []
    for r in rows:
        cells.append(
            "<tr>"
            f"<td>{_esc(r['period'])}</td>"
            f"<td>{r['calls']}</td>"
            f"<td>${r['total_cost']:.6f}</td>"
            f'<td><div class="bar"><div class="fill" style="width:{_bar(r["total_cost"], max_cost)}"></div></div></td>'
            "</tr>"
        )
    return "".join(cells)


def _usage_model_rows(data: dict[str, Any]) -> str:
    rows = data["usage"]["by_model"]
    if not rows:
        return '<tr><td colspan="4" class="muted">No usage recorded yet.</td></tr>'
    max_tokens = max(r["tokens"] for r in rows) or 1
    cells = []
    for r in rows:
        cells.append(
            "<tr>"
            f"<td>{_esc(r['model'])}</td>"
            f"<td>{r['requests']}</td>"
            f"<td>{r['tokens']:,}</td>"
            f"<td>${r['cost_usd']:.6f}</td>"
            f'<td><div class="bar"><div class="fill" style="width:{_bar(float(r["tokens"]), float(max_tokens))}"></div></div></td>'
            "</tr>"
        )
    return "".join(cells)


def _recent_usage_rows(data: dict[str, Any]) -> str:
    rows = data["usage"]["recent"]
    if not rows:
        return '<tr><td colspan="6" class="muted">No requests yet.</td></tr>'
    cells = []
    for r in rows:
        badge = "ok" if r["status"] == "success" else "err"
        cells.append(
            "<tr>"
            f"<td>{_esc(r['time'])}</td>"
            f"<td>{_esc(r['model'])}</td>"
            f"<td>{r['tokens']:,}</td>"
            f"<td>${r['cost_usd']:.6f}</td>"
            f"<td>{r['latency_ms']:.0f} ms</td>"
            f'<td><span class="badge {badge}">{_esc(r["status"])}</span></td>'
            "</tr>"
        )
    return "".join(cells)


def _drift_rows(data: dict[str, Any]) -> str:
    drift = data["drift"]
    metrics = drift.get("metrics") or {}
    if not metrics:
        return f'<tr><td colspan="5" class="muted">{_esc(drift.get("error") or "No drift metrics yet — insufficient telemetry to compare windows.")}</td></tr>'
    cells = []
    for name, m in sorted(metrics.items()):
        flag = "drift" if m.get("drifting") else "none"
        cells.append(
            "<tr>"
            f"<td>{_esc(name)}</td>"
            f'<td><span class="badge {flag}">{_esc(m.get("direction"))}</span></td>'
            f"<td>{m.get('recent_mean', 0.0):.4f}</td>"
            f"<td>{m.get('baseline_mean', 0.0):.4f}</td>"
            f"<td>{_esc(m.get('zscore', 0.0))}</td>"
            f"<td>{m.get('samples_recent', 0)} / {m.get('samples_baseline', 0)}</td>"
            f"<td>{_esc(m.get('detail'))}</td>"
            "</tr>"
        )
    return "".join(cells)


def _eval_metric_rows(data: dict[str, Any]) -> str:
    rows = data["evaluations"]["by_metric"]
    if not rows:
        return '<tr><td colspan="4" class="muted">No evaluation logs yet — run /admin/evaluation endpoints to seed the scorecard.</td></tr>'
    cells = []
    for r in rows:
        width = f"{r['pass_rate'] * 100:.0f}%"
        cells.append(
            "<tr>"
            f"<td>{_esc(r['metric'])}</td>"
            f"<td>{r['count']}</td>"
            f"<td>{r['passed']}</td>"
            f"<td>{r['pass_rate']:.1%}</td>"
            f'<td><div class="bar"><div class="fill ok" style="width:{width}"></div></div></td>'
            "</tr>"
        )
    return "".join(cells)


def render_viewer_html(data: dict[str, Any]) -> str:
    """Server-render the full dashboard — pure Python string assembly, no deps."""
    stack = data["stack"]
    newrelic = stack["newrelic"]
    usage = data["usage"]
    latency = usage.get("latency")
    drift = data["drift"]
    telemetry = data["telemetry"]

    latency_html = (
        "<tr>"
        f"<td>{latency['count']}</td><td>{latency['avg_ms']} ms</td>"
        f"<td>{latency['p50_ms']} ms</td><td>{latency['p95_ms']} ms</td><td>{latency['p99_ms']} ms</td>"
        "</tr>"
        if latency
        else '<tr><td colspan="5" class="muted">No latency samples yet.</td></tr>'
    )

    counter_html = "".join(
        f"<div class='chip'><b>{_esc(k)}</b><span>{v}</span></div>" for k, v in telemetry.get("counters", {}).items()
    ) or '<span class="muted">No counters recorded this process.</span>'
    error_html = "".join(
        f"<div class='chip'><b>{_esc(k)}</b><span>{v}</span></div>" for k, v in telemetry.get("errors", {}).items()
    ) or '<span class="muted">No errors recorded this process.</span>'

    drift_banner = (
        f'<div class="banner banner-drift">⚠ Drift detected across {drift.get("drifting_metrics")} metric(s).</div>'
        if drift.get("detected")
        else '<div class="banner banner-ok">✓ No drift detected in the latest vs baseline windows.</div>'
    )

    status = newrelic.get("status", "inactive")
    nr_badge = "ok" if status == "live" else "warn"

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Nexus · Observability</title>
<style>
  :root {{ --bg:#0f1420; --card:#171e2e; --line:#26324a; --text:#dbe4f3; --dim:#8494b0;
          --acc:#4f8cff; --ok:#2fbf71; --warn:#e8a13d; --err:#e5534b; }}
  * {{ box-sizing:border-box; }}
  body {{ margin:0; background:var(--bg); color:var(--text); font:14px/1.5 -apple-system,"Segoe UI",system-ui,sans-serif; }}
  header {{ padding:18px 24px; border-bottom:1px solid var(--line); display:flex; align-items:center; gap:14px; flex-wrap:wrap; }}
  header h1 {{ margin:0; font-size:18px; letter-spacing:.4px; }}
  header .sub {{ color:var(--dim); font-size:12px; }}
  main {{ padding:20px 24px; max-width:1180px; margin:0 auto; }}
  section {{ background:var(--card); border:1px solid var(--line); border-radius:10px; padding:16px 18px; margin-bottom:18px; }}
  h2 {{ margin:0 0 12px; font-size:14px; text-transform:uppercase; letter-spacing:1px; color:var(--acc); }}
  table {{ width:100%; border-collapse:collapse; font-size:13px; }}
  th,td {{ text-align:left; padding:7px 10px; border-bottom:1px solid var(--line); }}
  th {{ color:var(--dim); font-weight:600; font-size:12px; text-transform:uppercase; letter-spacing:.5px; }}
  td.num,th.num {{ text-align:right; font-variant-numeric:tabular-nums; }}
  .muted {{ color:var(--dim); }}
  .badge {{ display:inline-block; padding:1px 8px; border-radius:999px; font-size:11px; font-weight:600; }}
  .badge.ok {{ background:rgba(47,191,113,.16); color:var(--ok); }}
  .badge.warn {{ background:rgba(232,161,61,.16); color:var(--warn); }}
  .badge.err,.badge.drift {{ background:rgba(229,83,75,.16); color:var(--err); }}
  .badge.none {{ background:rgba(132,148,176,.16); color:var(--dim); }}
  .bar {{ height:10px; width:140px; background:var(--line); border-radius:5px; overflow:hidden; }}
  .fill {{ height:100%; background:var(--acc); }}
  .fill.ok {{ background:var(--ok); }}
  .chips {{ display:flex; flex-wrap:wrap; gap:8px; }}
  .chip {{ background:var(--bg); border:1px solid var(--line); border-radius:8px; padding:4px 10px; font-size:12px; color:var(--dim); }}
  .chip b {{ display:block; color:var(--text); }}
  .banner {{ padding:10px 14px; border-radius:8px; font-weight:600; margin-bottom:12px; }}
  .banner-drift {{ background:rgba(229,83,75,.12); border:1px solid rgba(229,83,75,.4); color:var(--err); }}
  .banner-ok {{ background:rgba(47,191,113,.12); border:1px solid rgba(47,191,113,.4); color:var(--ok); }}
  .grid2 {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(220px,1fr)); gap:12px; }}
  .kpi {{ background:var(--bg); border:1px solid var(--line); border-radius:8px; padding:12px 14px; }}
  .kpi b {{ display:block; font-size:22px; }}
  .kpi span {{ color:var(--dim); font-size:12px; }}
  footer {{ color:var(--dim); font-size:12px; padding:0 24px 24px; }}
  a {{ color:var(--acc); }}
</style>
</head>
<body>
<header>
  <h1>Nexus · Observability</h1>
  <span class="sub">Admin viewer · as of <b>{_esc(data['as_of'])}</b> · data from usage_logs / cost_logs / evaluation_logs</span>
</header>
<main>

  <section>
    <h2>Stack status</h2>
    <div class="grid2">
      <div class="kpi"><span>New Relic bridge</span><b class="badge {nr_badge}">{_esc(status)}</b></div>
      <div class="kpi"><span>Drift monitoring</span><b>{'on' if stack['drift_monitoring'].get('enabled') else 'off'}</b></div>
      <div class="kpi"><span>Audit logs</span><b>{'on' if stack['audit_logs'] else 'off'}</b></div>
      <div class="kpi"><span>PII redaction</span><b>{'on' if (stack['pii_redaction'].get('enabled')) else 'off'}</b></div>
      <div class="kpi"><span>In-process metrics</span><b>{'on' if stack['metrics_collector'] else 'off'}</b></div>
    </div>
    <p class="muted" style="margin-top:10px">New Relic activation: <code>{_esc(newrelic.get('activate'))}</code>
    (enabled={str(newrelic.get('enabled')).lower()}, package installed={str(newrelic.get('package_installed')).lower()}).</p>
  </section>

  <section>
    <h2>Cost · total ${data['cost']['total_usd']:.6f} across {data['cost']['log_count']} cost-log rows</h2>
    <table><thead><tr><th>Model</th><th class="num">Logs</th><th class="num">Total</th><th class="num">Input</th><th class="num">Output</th><th>Share</th></tr></thead>
    <tbody>{_cost_model_rows(data)}</tbody></table>
    <h2 style="margin-top:16px">Cost by billing period</h2>
    <table><thead><tr><th>Period</th><th class="num">Logs</th><th class="num">Total</th><th>Share</th></tr></thead>
    <tbody>{_cost_period_rows(data)}</tbody></table>
  </section>

  <section>
    <h2>Usage telemetry · {usage['request_count']} requests · {usage['error_rate']:.1%} error rate ({usage['error_count']} errors)</h2>
    <table><thead><tr><th class="num">Samples</th><th class="num">Avg</th><th class="num">P50</th><th class="num">P95</th><th class="num">P99</th></tr></thead>
    <tbody>{latency_html}</tbody></table>
    <h2 style="margin-top:16px">Tokens & cost by model</h2>
    <table><thead><tr><th>Model</th><th class="num">Requests</th><th class="num">Tokens</th><th class="num">Cost</th><th>Share</th></tr></thead>
    <tbody>{_usage_model_rows(data)}</tbody></table>
    <h2 style="margin-top:16px">Recent requests (last {_RECENT_ROWS})</h2>
    <table><thead><tr><th>Time</th><th>Model</th><th class="num">Tokens</th><th class="num">Cost</th><th class="num">Latency</th><th>Status</th></tr></thead>
    <tbody>{_recent_usage_rows(data)}</tbody></table>
  </section>

  <section>
    <h2>Drift report</h2>
    {drift_banner}
    <table><thead><tr><th>Metric</th><th>Direction</th><th class="num">Recent mean</th><th class="num">Baseline mean</th><th class="num">z-score</th><th class="num">Samples (recent/baseline)</th><th>Detail</th></tr></thead>
    <tbody>{_drift_rows(data)}</tbody></table>
  </section>

  <section>
    <h2>Evaluation scorecard · {data['evaluations']['log_count']} logged evaluations</h2>
    <table><thead><tr><th>Metric</th><th class="num">Runs</th><th class="num">Passed</th><th class="num">Pass rate</th><th>Pass rate</th></tr></thead>
    <tbody>{_eval_metric_rows(data)}</tbody></table>
    <h2 style="margin-top:16px">Recent evaluations (last {_RECENT_ROWS})</h2>
    <table><thead><tr><th>Time</th><th>Metric</th><th class="num">Score</th><th>Passed</th><th>Evaluator</th><th>Reason</th></tr></thead>
    <tbody>{''.join(_recent_eval_html(r) for r in data['evaluations']['recent']) or '<tr><td colspan="6" class="muted">No evaluation logs yet.</td></tr>'}</tbody></table>
  </section>

  <section>
    <h2>In-process counters & errors</h2>
    <div class="chips">{counter_html}</div>
    <div class="chips" style="margin-top:8px">{error_html}</div>
  </section>

</main>
<footer>Zero-dependency viewer · endpoints:
  <a href="/api/v1/admin/monitoring/observability">JSON status</a> ·
  <a href="/api/v1/admin/monitoring/viewer-data">viewer JSON</a> ·
  <a href="/api/v1/admin/monitoring/drift">drift JSON</a> ·
  <a href="/api/v1/admin/system-status">system status JSON</a>
</footer>
</body>
</html>
"""


def _recent_eval_html(r: dict[str, Any]) -> str:
    badge = "ok" if r["passed"] else "err"
    return (
        "<tr>"
        f"<td>{_esc(r['time'])}</td>"
        f"<td>{_esc(r['metric'])}</td>"
        f"<td class='num'>{r['score']:.3f}</td>"
        f'<td><span class="badge {badge}">{"pass" if r["passed"] else "fail"}</span></td>'
        f"<td>{_esc(r['evaluator'])}</td>"
        f"<td>{_esc(r['reason'])}</td>"
        "</tr>"
    )
