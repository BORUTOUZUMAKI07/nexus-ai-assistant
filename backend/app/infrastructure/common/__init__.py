"""Shared infrastructure utilities (event-loop selection, etc.)."""

from backend.app.infrastructure.common.event_loop import event_loop_factory

__all__ = ["event_loop_factory"]
