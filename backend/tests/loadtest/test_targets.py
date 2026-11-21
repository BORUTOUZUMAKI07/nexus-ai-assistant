"""Guard the Locust harness against drifting back to obsolete endpoints.

The old harness posted to ``/api/v1/chat`` (no such route) and called
``/api/v1/auth/me`` without a token, so every run reported false failures.
"""

from __future__ import annotations

import importlib.util
import tempfile
from pathlib import Path
from types import ModuleType

REPO_ROOT = Path(__file__).resolve().parents[3]
LOADTEST_DIR = REPO_ROOT / "scripts" / "loadtest"
TARGETS = LOADTEST_DIR / "targets.py"
LOCUSTFILE = LOADTEST_DIR / "locustfile.py"
RUN_GATE = LOADTEST_DIR / "run_gate.py"


def _load(path: Path, name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_chat_target_uses_the_real_messages_route() -> None:
    targets = _load(TARGETS, "nexus_loadtest_targets")
    assert targets.chat_path("conv-123") == "/api/v1/conversations/conv-123/messages"


def test_auth_header_is_a_bearer_token() -> None:
    targets = _load(TARGETS, "nexus_loadtest_targets")
    assert targets.auth_headers("tok") == {"Authorization": "Bearer tok"}


def test_harness_uses_shared_targets() -> None:
    source = LOCUSTFILE.read_text(encoding="utf-8")
    assert '"/api/v1/chat"' not in source
    assert "from targets import" in source


def test_user_authenticates_before_tasks() -> None:
    source = LOCUSTFILE.read_text(encoding="utf-8")
    assert "def on_start" in source


def test_gate_results_path_is_portable() -> None:
    targets = _load(TARGETS, "nexus_loadtest_targets")
    assert Path(targets.RESULTS_PREFIX).parent == Path(tempfile.gettempdir())


def test_gate_uses_shared_results_prefix() -> None:
    source = RUN_GATE.read_text(encoding="utf-8")
    assert "RESULTS_PREFIX" in source
    assert '"/tmp/locust_results"' not in source
