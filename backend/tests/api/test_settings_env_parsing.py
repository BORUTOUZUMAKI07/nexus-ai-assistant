"""CORS_ORIGINS / TRUSTED_PROXY_IPS must accept the documented plain-string form.

Regression guard for a production outage: `CORS_ORIGINS` is typed `list[str]`,
and pydantic-settings JSON-decodes any complex field's env value *inside the
settings source*, before `parse_cors` (mode="before") can run. A perfectly
ordinary value -- `https://app.vercel.app` -- therefore raised

    pydantic_settings.exceptions.SettingsError: error parsing value for field
    "CORS_ORIGINS" from source "EnvSettingsSource"

at `settings = Settings()` during import. The API process died before uvicorn
could bind, so the platform reported only "deploy failed" with no application
traceback. `enable_decoding=False` in SettingsConfigDict is the fix; these tests
pin both accepted spellings so a future config edit cannot silently restore the
crash.
"""

from __future__ import annotations

import pytest
from backend.app.core.config import Settings


def _settings(monkeypatch: pytest.MonkeyPatch, **env: str) -> Settings:
    """Build Settings from the environment only -- never from a .env file."""
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    return Settings(_env_file=None)


def test_cors_origins_accepts_a_comma_separated_string(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _settings(
        monkeypatch,
        CORS_ORIGINS="https://nexus.vercel.app,https://staging.example.com",
    )
    assert settings.CORS_ORIGINS == [
        "https://nexus.vercel.app",
        "https://staging.example.com",
    ]


def test_cors_origins_accepts_a_json_array(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _settings(monkeypatch, CORS_ORIGINS='["https://nexus.vercel.app"]')
    assert settings.CORS_ORIGINS == ["https://nexus.vercel.app"]


def test_a_placeholder_prefix_does_not_crash_startup(monkeypatch: pytest.MonkeyPatch) -> None:
    """The exact value that took the Render service down.

    It is nonsense as an origin -- that is a configuration mistake the CORS
    middleware will reject at request time -- but it must not stop the process
    from importing settings. A deploy that fails to boot gives the operator no
    signal about which variable is wrong.
    """
    settings = _settings(monkeypatch, CORS_ORIGINS="REPLACE_WITH_VERCEL_URL")
    assert settings.CORS_ORIGINS == ["REPLACE_WITH_VERCEL_URL"]


def test_trusted_proxy_ips_accepts_a_comma_separated_string(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _settings(monkeypatch, TRUSTED_PROXY_IPS="10.0.0.1,10.0.0.0/8")
    assert settings.TRUSTED_PROXY_IPS == ["10.0.0.1", "10.0.0.0/8"]


def test_trusted_proxy_ips_accepts_a_json_array(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _settings(monkeypatch, TRUSTED_PROXY_IPS='["192.168.0.1"]')
    assert settings.TRUSTED_PROXY_IPS == ["192.168.0.1"]
