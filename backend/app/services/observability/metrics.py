"""
Metrics & System Performance Telemetry.
Tracks latency percentiles, error rates, tool call frequency, and cache hit metrics.
"""
from collections import defaultdict
from typing import Any

import structlog

logger = structlog.get_logger(__name__)


class MetricsCollector:
    """In-memory lightweight metrics aggregator."""

    def __init__(self) -> None:
        self._latencies: dict[str, list[float]] = defaultdict(list)
        self._counts: dict[str, int] = defaultdict(int)
        self._errors: dict[str, int] = defaultdict(int)

    def record_latency(self, operation: str, duration_ms: float) -> None:
        self._latencies[operation].append(duration_ms)
        # Keep sliding buffer of last 1,000 samples
        if len(self._latencies[operation]) > 1000:
            self._latencies[operation].pop(0)

    def increment(self, counter_name: str, count: int = 1) -> None:
        self._counts[counter_name] += count

    def record_error(self, error_type: str) -> None:
        self._errors[error_type] += 1

    def get_summary(self) -> dict[str, Any]:
        stats: dict[str, Any] = {
            "counters": dict(self._counts),
            "errors": dict(self._errors),
            "latency_stats": {},
        }
        for op, samples in self._latencies.items():
            if samples:
                sorted_samples = sorted(samples)
                n = len(sorted_samples)
                stats["latency_stats"][op] = {
                    "count": n,
                    "avg_ms": round(sum(sorted_samples) / n, 2),
                    "p50_ms": round(sorted_samples[int(n * 0.50)], 2),
                    "p95_ms": round(sorted_samples[int(n * 0.95)], 2),
                    "p99_ms": round(sorted_samples[int(n * 0.99)], 2),
                }
        return stats


def _escape_label_value(value: str) -> str:
    """Escape a Prometheus label value per the text exposition format (v0.0.4):
    backslash, double-quote and newline are the only characters that need
    escaping inside a quoted label value."""
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def render_prometheus_text(collector: "MetricsCollector | None" = None) -> str:
    """Render the collector as Prometheus text exposition format (v0.0.4).

    Zero runtime dependencies: emits ``# HELP`` / ``# TYPE`` metadata plus the
    per-series samples so a standard scrape (or ``promtool check metrics``) can
    ingest it. Latency percentiles are exported in *seconds* under a proper
    ``summary`` family (``_sum`` + ``_count`` suffixes) so Prometheus SLO alerts
    can ``rate()`` / ``histogram_quantile()`` over them.

    Families:
      * ``nexus_requests_total{operation}`` — counters (tool calls, experiments)
      * ``nexus_errors_total{error}``       — error spans by origin
      * ``nexus_latency_seconds{operation,quantile}`` summary (+ _sum/_count)
      * ``nexus_latency_avg_seconds{operation}`` gauge for quick scalar checks
    """
    collector = collector or metrics_collector
    lines: list[str] = []

    lines.append("# HELP nexus_requests_total Requests recorded by the metrics collector.")
    lines.append("# TYPE nexus_requests_total counter")
    for name, value in sorted(collector._counts.items()):
        lines.append(
            f'nexus_requests_total{{operation="{_escape_label_value(name)}"}} {value}'
        )

    lines.append("# HELP nexus_errors_total Errors recorded by exception spans / gateways.")
    lines.append("# TYPE nexus_errors_total counter")
    for name, value in sorted(collector._errors.items()):
        lines.append(
            f'nexus_errors_total{{error="{_escape_label_value(name)}"}} {value}'
        )

    for op, stats in sorted(collector.get_summary()["latency_stats"].items()):
        label = f'operation="{_escape_label_value(op)}"'
        count = int(stats["count"])
        avg_s = float(stats["avg_ms"]) / 1000.0
        lines.append("# HELP nexus_latency_seconds Latency distribution per operation (seconds).")
        lines.append("# TYPE nexus_latency_seconds summary")
        for quantile, key in (("0.5", "p50_ms"), ("0.95", "p95_ms"), ("0.99", "p99_ms")):
            lines.append(
                f'nexus_latency_seconds{{{label},quantile="{quantile}"}} '
                f'{float(stats[key]) / 1000.0}'
            )
        lines.append(f"nexus_latency_seconds_sum{{{label}}} {avg_s * count}")
        lines.append(f"nexus_latency_seconds_count{{{label}}} {count}")
        lines.append("# HELP nexus_latency_avg_seconds Average latency per operation (seconds).")
        lines.append("# TYPE nexus_latency_avg_seconds gauge")
        lines.append(f"nexus_latency_avg_seconds{{{label}}} {avg_s}")

    return "\n".join(lines) + "\n"


metrics_collector = MetricsCollector()
