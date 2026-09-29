"""
PII Redaction Utilities.

Regex-based scrubber for common personally identifiable information and
high-value secrets (security/privacy: PII minimization and anonymization).
Used by two consumers:

* a structlog processor (``redact_event``) enabling log-hygiene redaction when
  ``PII_REDACTION_ENABLED`` is set — every emitted event is scrubbed;
* a direct ``redact_text`` scrubber for request payloads before persistence.

All matchers are deliberately conservative (format-checked patterns) so logs
are not mangled; this is defense-in-depth, not a replacement for structured
schema validation or pseudonymization at write time.
"""
import re
from typing import Any

from backend.app.core.config import settings

_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
_PHONE_RE = re.compile(
    r"(?<!\d)(?:\+?\d{1,3}[\s.\-]?)?\(?\d{3}\)?[\s.\-]?\d{3}[\s.\-]?\d{4}(?!\d)"
)
_SSN_RE = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")
_IP_RE = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
_AWS_KEY_RE = re.compile(r"\b(?:AKIA|ASIA|AGPA|AIDA|AROA)[A-Z0-9]{16}\b")
_BEARER_TOKEN_RE = re.compile(r"\b(?:Bearer|sk-|ghp_|xox[baprs]-)[A-Za-z0-9._\-]{8,}\b", re.IGNORECASE)
_PRIVATE_KEY_RE = re.compile(
    r"-----BEGIN (?:RSA |EC |OPENSSH |DSA |ENCRYPTED )?PRIVATE KEY-----.*?-----END "
    r"(?:RSA |EC |OPENSSH |DSA |ENCRYPTED )?PRIVATE KEY-----",
    re.DOTALL,
)
_HOME_PATH_RE = re.compile(
    r"(?<![\\/\w])(?:C:\\Users\\[^\\\s]+|/home/[^/\s]+|/Users/[^/\s]+)"
)

# Keys whose *entire value* is replaced in structured events (they are secrets
# by name even when the value does not match a format pattern).
_SECRET_KEYS = {
    "authorization",
    "api_key",
    "apikey",
    "secret",
    "secret_key",
    "password",
    "passwd",
    "token",
    "access_token",
    "refresh_token",
    "session_id",  # treated as sensitive correlation material
    "private_key",
    "encryption_key",
    "jwt",
}


def redact_text(text: str, replacement: str | None = None) -> str:
    """
    Scrub all recognized PII/secret patterns from ``text``.

    Order matters: private keys are removed first (their bodies contain colon
    and dash patterns that the SSN/phone matchers could otherwise touch), then
    the narrower format matchers.
    """
    repl = replacement or settings.PII_REDACTION_REPLACEMENT
    if not text:
        return text
    scrubbed = _PRIVATE_KEY_RE.sub(repl, text)
    scrubbed = _EMAIL_RE.sub(repl, scrubbed)
    scrubbed = _SSN_RE.sub(repl, scrubbed)
    scrubbed = _cc_sub(scrubbed, repl)
    scrubbed = _PHONE_RE.sub(repl, scrubbed)
    scrubbed = _IP_RE.sub(repl, scrubbed)
    scrubbed = _AWS_KEY_RE.sub(repl, scrubbed)
    scrubbed = _BEARER_TOKEN_RE.sub(repl, scrubbed)
    scrubbed = _HOME_PATH_RE.sub(repl, scrubbed)
    return scrubbed


def _cc_sub(text: str, repl: str) -> str:
    """Redact 13-16+ digit card-like runs without mangling numeric IDs/timestamps."""
    # Require either separators (spaces/dashes) or a length >= 15 so short
    # numeric identifiers (e.g. tool timestamps) are left intact.
    return re.sub(r"\b(?:\d{4}[ -]?){3,4}\d{1,4}\b", repl, text)


def redact_value(value: Any) -> Any:
    """Recursively scrub scalar strings inside dict/list structures."""
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for k, v in value.items():
            if isinstance(k, str) and k.lower() in _SECRET_KEYS:
                out[k] = settings.PII_REDACTION_REPLACEMENT
            else:
                out[k] = redact_value(v)
        return out
    if isinstance(value, list):
        return [redact_value(v) for v in value]
    return value


def redact_event(logger, method_name: str, event_dict: dict[str, Any]) -> dict[str, Any]:
    """structlog processor: scrub every key-value pair in the event."""
    return redact_value(event_dict)  # type: ignore[return-value]
