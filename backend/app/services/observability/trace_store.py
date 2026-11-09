"""
Trace store for side-by-side trace comparison.

Reads OpenTelemetry spans from the configured exporter backend and returns
them as JSON for the /api/v1/admin/evaluation/traces/* endpoints.

Currently reads from the in-process span buffer if the OTel SDK is configured
with a memory exporter; otherwise returns a graceful not-found.
"""
from __future__ import annotations

from typing import Any


async def get_trace_span(trace_id: str) -> dict[str, Any] | None:
    """
    Return a single trace span as a JSON-serializable dict, or None if not
    found.

    In production this reads from the OTel collector / tracing backend. For
    now it returns None (graceful not-found) so the compare endpoint
    degrades cleanly without a tracing backend configured.
    """
    # Placeholder: integrate with the OTel SDK's in-memory span exporter or
    # a tracing backend (Jaeger, Tempo, Honeycomb, etc.) here.
    return None
