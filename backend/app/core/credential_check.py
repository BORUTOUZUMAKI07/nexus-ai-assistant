"""
Startup validation of configured provider credentials.

The defect this fixes: a missing or placeholder credential was discovered at
the point of use, in the middle of a user's chat turn. The observable symptom
was a silent capability downgrade — a search provider returning nothing, the
app quietly falling through to a worse fallback, and nothing in the logs
saying *why*. The only clue was a `print()` in an exception handler.

The inventory behind this module was read out of the source rather than
guessed: every ``settings.*`` attribute matching KEY / TOKEN / SECRET / DSN /
PASSWORD across ``app/``. Fourteen of them are real external credentials.

Two rules govern what counts as a problem:

* **Only validate what is configured to be used.** An unset optional provider
  is a valid deployment, not an error. A *configured* provider that cannot work
  is the bug.
* **Never log a secret.** Only the variable name, whether it looks like a
  placeholder, and length. This module is called at startup, so anything it
  prints lands in the log aggregator permanently.

Fails open by design. A misconfigured provider degrades that one capability;
it must not stop the process from booting and taking every other capability
down with it. The findings are returned so the caller can decide, and the
caller in ``main.py`` logs them without raising.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

#: Substrings that mean "this is a template value, not a real key". Both
#: cases are real: `.env.example` ships ``fc_placeholder...`` and some compose
#: files interpolate a literal that never gets replaced.
_PLACEHOLDER_MARKERS = (
    "placeholder",
    "changeme",
    "change_me",
    "your_",
    "your-",
    "xxx",
    "todo",
    "replace_me",
    "replace-me",
    "example",
    "dummy",
    "notset",
    "none",
    "null",
    "undefined",
    "<",
)

#: Secret *shapes* worth sanity-checking, so an obviously-truncated key is
#: caught at boot rather than at 401. Lengths are the floor seen in the wild;
#: a real key is always comfortably longer.
_MIN_SECRET_LENGTH = 12

#: (settings attribute, human name, required?)
#: Read out of the source, not invented. "Required" means the feature cannot
#: work at all without it; optional entries are only reported when set-but-broken.
_CREDENTIALS: tuple[tuple[str, str, bool], ...] = (
    ("DATABASE_URL", "PostgreSQL", True),
    ("REDIS_URL", "Redis", False),
    ("JWT_SECRET_KEY", "JWT signing", True),
    ("SECRET_KEY", "app secret", False),
    ("ENCRYPTION_KEY", "field encryption", True),
    ("OPENROUTER_API_KEY", "LiteLLM / OpenRouter", False),
    ("GROQ_API_KEY", "LiteLLM / Groq", False),
    ("GEMINI_API_KEY", "LiteLLM / Gemini", False),
    ("TAVILY_API_KEY", "web search (primary)", False),
    ("FIRECRAWL_API_KEY", "web scrape (primary)", False),
    ("QDRANT_API_KEY", "Qdrant", False),
    ("MEM0_API_KEY", "long-term memory", False),
    ("E2B_API_KEY", "code execution sandbox", False),
    ("RESEND_API_KEY", "transactional email", False),
    ("SMTP_PASSWORD", "SMTP email", False),
    ("SUPABASE_URL", "Supabase storage", False),
    ("SUPABASE_SERVICE_ROLE_KEY", "Supabase storage", False),
    ("GOOGLE_OAUTH_CLIENT_SECRET", "Google SSO", False),
    ("GITHUB_OAUTH_CLIENT_SECRET", "GitHub SSO", False),
    ("MCP_API_KEY", "MCP gateway", False),
    ("SENTRY_DSN", "Sentry", False),
    ("LANGGRAPH_CHECKPOINT_DSN", "LangGraph checkpointer", False),
)


@dataclass(frozen=True)
class Finding:
    """One credential problem. Carries no secret material, ever."""

    variable: str
    provider: str
    severity: str  # "error" | "warning"
    problem: str
    required: bool = False

    def as_log_kwargs(self) -> dict[str, Any]:
        return {
            "variable": self.variable,
            "provider": self.provider,
            "problem": self.problem,
            "required": self.required,
        }


@dataclass
class ValidationReport:
    findings: list[Finding] = field(default_factory=list)
    checked: int = 0

    @property
    def errors(self) -> list[Finding]:
        return [f for f in self.findings if f.severity == "error"]

    @property
    def warnings(self) -> list[Finding]:
        return [f for f in self.findings if f.severity == "warning"]

    @property
    def ok(self) -> bool:
        return not self.errors

    def summary(self) -> str:
        if not self.findings:
            return f"{self.checked} credentials checked, all usable"
        return (
            f"{self.checked} credentials checked, {len(self.errors)} error(s), "
            f"{len(self.warnings)} warning(s)"
        )


def looks_like_placeholder(value: str) -> str | None:
    """Return a short reason if the value is obviously not a real credential.

    Returns None when the value looks usable. The value itself is never
    returned or embedded, so the result is safe to log.
    """
    if not value or not value.strip():
        return "empty"
    lowered = value.strip().lower()
    for marker in _PLACEHOLDER_MARKERS:
        if marker in lowered:
            return f"looks like a placeholder ({marker})"
    return None


def validate_credential(name: str, value: Any, required: bool) -> Finding | None:
    """Check one credential. Returns a Finding only when there is a problem."""
    if value is None or (isinstance(value, str) and not value.strip()):
        if required:
            return Finding(name, name, "error", "required but not set", True)
        return None  # optional and unset: a valid deployment

    if not isinstance(value, str):
        # A non-string here is a type error in .env, not a missing key.
        return Finding(name, name, "warning", f"expected a string, got {type(value).__name__}")

    reason = looks_like_placeholder(value)
    if reason:
        # Configured-but-placeholder: the worst case, because the app believes
        # the capability is available and silently degrades at request time.
        return Finding(name, name, "error" if required else "warning", reason, required)

    if len(value.strip()) < _MIN_SECRET_LENGTH and not _looks_like_url(name, value):
        return Finding(
            name,
            name,
            "warning",
            f"suspiciously short ({len(value.strip())} chars)",
        )

    return None


def _looks_like_url(name: str, value: str) -> bool:
    """DSNs and endpoints are legitimately shorter than an API key."""
    return "URL" in name or "DSN" in name or value.strip().startswith(("http://", "https://", "postgresql://", "redis://"))


def validate_settings(settings_obj: Any) -> ValidationReport:
    """Validate every credential in the inventory against a settings object.

    Takes the object rather than importing the global, so it is testable
    without mutating process state — and so a caller can validate a
    candidate config before promoting it.
    """
    report = ValidationReport()

    for name, provider, required in _CREDENTIALS:
        report.checked += 1
        finding = validate_credential(name, getattr(settings_obj, name, None), required)
        if finding is not None:
            report.findings.append(
                Finding(finding.variable, provider, finding.severity, finding.problem, required)
            )

    # A JWT secret shorter than the HS256 block size is silently weak, and
    # nothing else in the codebase would notice. Checked after the generic pass
    # and replacing any finding it already produced, so one weak secret yields
    # one actionable line rather than two overlapping ones.
    jwt = getattr(settings_obj, "JWT_SECRET_KEY", None)
    if isinstance(jwt, str) and jwt.strip() and len(jwt.strip()) < 32:
        report.findings = [f for f in report.findings if f.variable != "JWT_SECRET_KEY"]
        report.findings.append(
            Finding(
                "JWT_SECRET_KEY",
                "JWT signing",
                "error",
                f"only {len(jwt.strip())} chars; HS256 wants at least 32 for a strong key",
                True,
            )
        )

    return report


def format_report(report: ValidationReport) -> list[str]:
    """Human-readable lines for a startup log. No secret material, by design."""
    lines = [report.summary()]
    for finding in report.findings:
        lines.append(
            f"  [{finding.severity}] {finding.variable} ({finding.provider}): {finding.problem}"
        )
    if report.findings:
        lines.append(
            "  Unconfigured optional providers are normal; a configured one that "
            "cannot work is what is reported here."
        )
    return lines


# Kept for callers that want to assert the inventory is exhaustive. Exposed
# rather than private so a test can walk it and check the settings module
# actually defines every name, which is what catches a rename.
CREDENTIAL_NAMES: tuple[str, ...] = tuple(
    dict.fromkeys(name for name, _p, _r in _CREDENTIALS)
)

#: Pattern every inventory entry must match, so a lowercase or dotted name
#: cannot be added by mistake. Asserted by a test rather than at import: an
#: ``assert`` in library code is stripped under ``python -O`` and bandit
#: (B101) rejects it outright.
CREDENTIAL_NAME_PATTERN = r"[A-Z][A-Z0-9_]*"
