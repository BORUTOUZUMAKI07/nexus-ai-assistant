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


metrics_collector = MetricsCollector()
