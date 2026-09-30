"""
Startup secret hygiene — a security control, so it is pinned by tests.

The production validator must refuse to boot with a blank secret or with any
value published in ``backend/.env.example``. The second case is the one that
regressed: the guard compared against placeholder strings from an older revision
of the example file, so copying the example to ``.env`` and flipping only
``ENVIRONMENT=production`` shipped an app signing JWTs with a key that is
public in this repository.
"""
from pathlib import Path

import pytest
from backend.app.core.config import PUBLISHED_EXAMPLE_SECRETS, Settings

ENV_EXAMPLE = Path(__file__).resolve().parents[1] / ".env.example"

STRONG_SECRET = "k" * 48
STRONG_ENCRYPTION = "e" * 64


def _example_values() -> dict[str, str]:
    """Read SECRET_KEY / ENCRYPTION_KEY straight out of .env.example."""
    found: dict[str, str] = {}
    for line in ENV_EXAMPLE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        if key in ("SECRET_KEY", "ENCRYPTION_KEY"):
            found[key] = value.strip().strip('"').strip("'")
    return found


def test_env_example_defines_both_secrets():
    values = _example_values()
    assert "SECRET_KEY" in values, ".env.example must still ship SECRET_KEY to document it"
    assert "ENCRYPTION_KEY" in values, ".env.example must still ship ENCRYPTION_KEY"


def test_guard_covers_every_secret_published_in_env_example():
    """Drift guard: a new example secret must also be refused in production."""
    published = {v for v in _example_values().values() if v}
    uncovered = published - PUBLISHED_EXAMPLE_SECRETS
    assert not uncovered, (
        "These values are in .env.example but not in PUBLISHED_EXAMPLE_SECRETS, so "
        "production would accept them as real secrets. Add each to the set: "
        f"{sorted(uncovered)}"
    )


def _production_settings(**overrides) -> Settings:
    """Build a production Settings with strong secrets unless overridden."""
    base = {"ENVIRONMENT": "production", "SECRET_KEY": STRONG_SECRET, "ENCRYPTION_KEY": STRONG_ENCRYPTION}
    base.update(overrides)
    return Settings(**base)


@pytest.mark.parametrize("name", ["SECRET_KEY", "ENCRYPTION_KEY"])
def test_production_refuses_the_published_example_value(name):
    with pytest.raises(ValueError, match=name):
        _production_settings(**{name: _example_values()[name]})


@pytest.mark.parametrize("name", ["SECRET_KEY", "ENCRYPTION_KEY"])
def test_production_refuses_a_blank_secret(name):
    with pytest.raises(ValueError, match=name):
        _production_settings(**{name: ""})


def test_production_reports_both_missing_secrets_at_once():
    """One error listing both, so a misconfigured deploy is fixed in one pass."""
    with pytest.raises(ValueError) as excinfo:
        _production_settings(SECRET_KEY="", ENCRYPTION_KEY="")
    message = str(excinfo.value)
    assert "SECRET_KEY" in message
    assert "ENCRYPTION_KEY" in message


def test_production_accepts_fresh_random_secrets():
    settings = _production_settings()
    assert settings.SECRET_KEY == STRONG_SECRET
    assert settings.ENCRYPTION_KEY == STRONG_ENCRYPTION


def test_jwt_secret_falls_back_to_secret_key():
    assert _production_settings().JWT_SECRET_KEY == STRONG_SECRET


def test_development_generates_secrets_when_absent():
    """Dev must stay runnable with no secrets configured at all."""
    settings = Settings(ENVIRONMENT="development", SECRET_KEY="", ENCRYPTION_KEY="")
    assert len(settings.SECRET_KEY) >= 32
    assert len(settings.ENCRYPTION_KEY) == 64
    assert settings.SECRET_KEY not in PUBLISHED_EXAMPLE_SECRETS
    assert settings.ENCRYPTION_KEY not in PUBLISHED_EXAMPLE_SECRETS


def test_development_replaces_a_published_example_value():
    """
    The old guard let a placeholder through in development, which is how the
    placeholder reached a .env in the first place. Regenerate instead.
    """
    example = _example_values()
    settings = Settings(
        ENVIRONMENT="development",
        SECRET_KEY=example["SECRET_KEY"],
        ENCRYPTION_KEY=example["ENCRYPTION_KEY"],
    )
    assert settings.SECRET_KEY != example["SECRET_KEY"]
    assert settings.ENCRYPTION_KEY != example["ENCRYPTION_KEY"]


def test_placeholder_set_has_no_duplicates_of_blank():
    """A blank entry would make every blank secret 'known' — harmless, but noise."""
    assert "" not in PUBLISHED_EXAMPLE_SECRETS
