"""The example env must not advertise services this app does not use.

Anthropic, Together, Celery, Sentry and Stripe have zero production call sites.
Leaving them in ``.env.example`` tells operators to provision credentials for
integrations that can never work, which is worse than omitting them.
"""

from pathlib import Path

ENV_EXAMPLE = Path(__file__).resolve().parents[2] / ".env.example"
DEAD_KEYS = ("ANTHROPIC_API_KEY", "TOGETHER_API_KEY", "CELERY_BROKER_URL", "SENTRY_DSN", "STRIPE_API_KEY")
LIVE_KEYS = ("GROQ_API_KEY", "GEMINI_API_KEY", "OPENROUTER_API_KEY", "MEM0_API_KEY")


def _active_keys() -> set[str]:
    keys: set[str] = set()
    for line in ENV_EXAMPLE.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped.startswith("#") or "=" not in stripped:
            continue
        key, _, _ = stripped.partition("=")
        keys.add(key.strip())
    return keys


def test_dead_service_keys_are_not_advertised():
    active = _active_keys()
    assert not (active & set(DEAD_KEYS)), f"dead keys still in .env.example: {sorted(active & set(DEAD_KEYS))}"


def test_dead_service_names_are_not_advertised_in_comments():
    text = ENV_EXAMPLE.read_text(encoding="utf-8").lower()
    for name in ("celery", "sentry", "stripe", "together ai"):
        assert name not in text, f".env.example still mentions unused service {name!r}"


def test_real_service_keys_are_still_documented():
    active = _active_keys()
    assert set(LIVE_KEYS) <= active, f"real keys accidentally removed: {sorted(set(LIVE_KEYS) - active)}"
