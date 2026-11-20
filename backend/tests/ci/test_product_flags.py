"""User-facing flags must be explicit in the deployment blueprint."""
from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[3]
BLUEPRINT = ROOT / "render.yaml"


def _backend_env() -> dict[str, object]:
    blueprint = yaml.safe_load(BLUEPRINT.read_text(encoding="utf-8"))
    backend = next(s for s in blueprint["services"] if s["name"] == "nexus-backend")
    return {str(v["key"]): v for v in backend["envVars"]}


def test_email_verification_is_enabled_in_the_deployment_blueprint():
    entry = _backend_env().get("EMAIL_VERIFICATION_REQUIRED")
    assert entry is not None, "the deployed backend must declare the flag explicitly"
    assert str(entry.get("value")).lower() == "true"
    assert entry.get("sync") is not True, "a boolean flag must never be secret-synced"


def test_response_cache_is_enabled_in_the_deployment_blueprint():
    entry = _backend_env().get("RESPONSE_CACHE_ENABLED")
    assert entry is not None, "the deployed backend must declare the flag explicitly"
    assert str(entry.get("value")).lower() == "true"
    assert entry.get("sync") is not True, "a boolean flag must never be secret-synced"
