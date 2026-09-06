"""
Guardrails and Moderation Service.
Implements PII redaction, prompt injection detection,
and input/output content filtering.
"""
import re

import structlog
from backend.app.core.exceptions import ValidationError

logger = structlog.get_logger(__name__)

# Common Prompt Injection patterns
INJECTION_PATTERNS = [
    r"ignore\s+(all\s+)?(previous|prior)\s+(instructions|directives|rules)",
    r"disregard\s+(all\s+)?(previous|prior)\s+prompts",
    r"reveal\s+(your\s+)?(system\s+prompt|instructions|initial\s+prompt)",
    r"you\s+are\s+now\s+in\s+developer\s+mode",
    r"dan\s+mode\s+enabled",
    r"always\s+respond\s+with\s+uncensored",
    r"jailbreak",
]

# Regex patterns for PII detection
PII_PATTERNS = {
    "credit_card": r"\b(?:\d{4}[-\s]?){3}\d{4}\b",
    "ssn": r"\b\d{3}-\d{2}-\d{4}\b",
    "email": r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,7}\b",
    "phone_us": r"\b(?:\+?1[-.\s]?)?\(?[2-9]\d{2}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b",
}


class GuardrailService:
    """
    Safety, privacy, and injection firewall.
    """

    def check_prompt_injection(self, text: str) -> tuple[bool, str]:
        """
        Scans input for known adversarial jailbreaks or prompt injection attempts.
        """
        for pattern in INJECTION_PATTERNS:
            if re.search(pattern, text, re.IGNORECASE):
                logger.warning("prompt_injection_detected", pattern=pattern)
                return True, f"Suspicious prompt pattern detected: '{pattern}'"
        return False, ""

    def redact_pii(self, text: str) -> tuple[str, dict[str, int]]:
        """
        Masks detected Personally Identifiable Information (PII) before storage or sending to LLMs.
        """
        redacted = text
        stats = {}

        for pii_type, pattern in PII_PATTERNS.items():
            matches = re.findall(pattern, redacted)
            if matches:
                stats[pii_type] = len(matches)
                redacted = re.sub(pattern, f"[{pii_type.upper()}_REDACTED]", redacted)

        if stats:
            logger.info("pii_redacted", pii_stats=stats)

        return redacted, stats

    def validate_input(self, text: str) -> str:
        """
        Complete input guardrail check. Returns sanitized text or raises ValidationError.
        """
        is_injection, reason = self.check_prompt_injection(text)
        if is_injection:
            raise ValidationError(f"Input rejected by safety guardrails: {reason}")

        sanitized, _ = self.redact_pii(text)
        return sanitized


guardrail_service = GuardrailService()
