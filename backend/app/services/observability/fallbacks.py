"""Count fail-open activations so silent degradation becomes visible.

The prefix is owned here rather than repeated at every call site: the dependency
health endpoint selects ``dependency_fallback:*`` counters from the shared
collector, so a typo in one site would otherwise hide that fallback forever.
"""

from backend.app.services.observability.metrics import metrics_collector

_PREFIX = "dependency_fallback:"


def count_fallback(name: str, count: int = 1) -> None:
    """Record that a fallback/degraded path was taken. Never raises."""
    metrics_collector.increment(f"{_PREFIX}{name}", count)
