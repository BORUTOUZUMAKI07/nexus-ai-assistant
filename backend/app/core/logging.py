"""
Nexus Production Logging Infrastructure.
Implements structured contextual JSON logging for production and human-readable
colorized console logging for local development.
Supports correlation IDs (X-Request-ID), user tracking, and exception formatting.
"""
import logging
import sys
from typing import Any

import structlog
from backend.app.core.config import settings

try:
    from asgi_correlation_id import correlation_id
except ImportError:
    correlation_id = None


def add_correlation_id(
    logger: logging.Logger, log_method: str, event_dict: dict[str, Any]
) -> dict[str, Any]:
    """Injects active request correlation ID into every log event if present."""
    if correlation_id and hasattr(correlation_id, "get"):
        cid = correlation_id.get()
        if cid:
            event_dict["request_id"] = cid
    return event_dict


def resolve_log_level() -> int:
    """Return the stdlib logging level the app should run at.

    ``settings.LOG_LEVEL`` wins when it is set; otherwise the level is derived
    from the environment, exactly as before: INFO in production, DEBUG
    everywhere else. Leaving LOG_LEVEL unset therefore reproduces the previous
    behaviour precisely.

    An unrecognised level is rejected loudly rather than silently falling back
    to a default — a typo'd LOG_LEVEL=VERBOSE that quietly did nothing is the
    exact failure this setting had before it was wired.
    """
    configured = (settings.LOG_LEVEL or "").strip()
    if not configured:
        return logging.INFO if settings.ENVIRONMENT == "production" else logging.DEBUG

    resolved = logging.getLevelNamesMapping().get(configured.upper())
    if resolved is None:
        valid = ", ".join(
            name for name, value in logging.getLevelNamesMapping().items()
            if isinstance(value, int)
        )
        raise ValueError(
            f"LOG_LEVEL={configured!r} is not a valid level. Use one of: {valid}."
        )
    return resolved


def setup_logging() -> None:
    """
    Configures structured logging across the entire application runtime.
    In production: Emits newline-delimited JSON with ISO timestamps and tracebacks.
    In development: Emits colorized console output with rich contextual key-value pairs.
    """
    log_level = resolve_log_level()
    shared_processors = [
        structlog.contextvars.merge_contextvars,
        add_correlation_id,
        structlog.stdlib.filter_by_level,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.stdlib.PositionalArgumentsFormatter(),
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        structlog.processors.UnicodeDecoder(),
    ]

    # Log-hygiene guard: scrub PII/secrets from every event before
    # rendering when enabled. Placed after exception formatting so tracebacks
    # are scrubbed too, and before the renderer so JSON output is clean.
    if settings.PII_REDACTION_ENABLED:
        from backend.app.core.redaction import redact_event

        shared_processors = shared_processors + [redact_event]

    if settings.ENVIRONMENT == "production":
        processors = shared_processors + [
            structlog.processors.dict_tracebacks,
            structlog.processors.JSONRenderer(),
        ]
    else:
        processors = shared_processors + [
            structlog.dev.ConsoleRenderer(colors=True),
        ]

    structlog.configure(
        processors=processors,
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=log_level,
    )

    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:  # nosec B110
            # encoding hint is best-effort; startup must not fail on it
            pass
    if hasattr(sys.stderr, "reconfigure"):
        try:
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
        except Exception:  # nosec B110
            # encoding hint is best-effort; startup must not fail on it
            pass

    # Third-party libraries emit chatty DEBUG/INFO records (httpcore's per-socket
    # ``close.started/close.complete`` trace, asyncio's loop-creation banner,
    # huggingface_hub's session closes) that are useless in our logs and, when
    # fired during interpreter shutdown, hit the already-finalized colorama
    # stream and print stdlib ``--- Logging error ---`` noise. Keep them quiet.
    for noisy_logger in (
        "asyncio",
        "datasets",
        "httpcore",
        "httpx",
        "huggingface_hub",
        "litellm",
        "LiteLLM",
        "LiteLLM Router",
        "LiteLLM Proxy",
    ):
        logging.getLogger(noisy_logger).setLevel(logging.WARNING)


# Root application logger instance
logger = structlog.get_logger("nexus")


def get_logger(name: str = "nexus") -> structlog.stdlib.BoundLogger:
    """Factory helper to obtain a named bound logger with module context."""
    return structlog.get_logger(name)
