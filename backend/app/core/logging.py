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


def setup_logging() -> None:
    """
    Configures structured logging across the entire application runtime.
    In production: Emits newline-delimited JSON with ISO timestamps and tracebacks.
    In development: Emits colorized console output with rich contextual key-value pairs.
    """
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
        level=logging.INFO if settings.ENVIRONMENT == "production" else logging.DEBUG,
    )


# Root application logger instance
logger = structlog.get_logger("nexus")


def get_logger(name: str = "nexus") -> structlog.stdlib.BoundLogger:
    """Factory helper to obtain a named bound logger with module context."""
    return structlog.get_logger(name)
