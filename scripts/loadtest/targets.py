"""Shared endpoint and header definitions for the load-test harness.

Keeping these in a pure module (no Locust import) makes them cheap to test and
prevents the harness from drifting onto routes the API does not expose.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

HEALTH_PATH = "/health"
CONVERSATIONS_PATH = "/api/v1/conversations"
AUTH_ME_PATH = "/api/v1/auth/me"
_CHAT_PATH_TEMPLATE = "/api/v1/conversations/{conversation_id}/messages"

#: Platform-neutral CSV prefix (``/tmp`` is not a real directory on Windows).
RESULTS_PREFIX = str(Path(tempfile.gettempdir()) / "nexus_locust_results")


def chat_path(conversation_id: str) -> str:
    """Path of the real synchronous message endpoint for one conversation."""
    return _CHAT_PATH_TEMPLATE.format(conversation_id=conversation_id)


def auth_headers(token: str) -> dict[str, str]:
    """Bearer headers for the authenticated tasks."""
    return {"Authorization": f"Bearer {token}"}
