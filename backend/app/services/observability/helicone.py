"""
Helicone Observability Integration.
Injects Helicone tracing headers for LLM inference calls, session correlation, and cost tracking.
"""

import structlog
from backend.app.core.config import settings

logger = structlog.get_logger(__name__)


class HeliconeService:
    """Provides Helicone observability headers for LiteLLM and direct HTTP requests."""

    @staticmethod
    def get_headers(
        user_id: str | None = None,
        conversation_id: str | None = None,
        session_name: str | None = None,
        properties: dict[str, str] | None = None,
    ) -> dict[str, str]:
        if not settings.HELICONE_API_KEY:
            return {}

        headers: dict[str, str] = {
            "Helicone-Auth": f"Bearer {settings.HELICONE_API_KEY}",
        }

        if user_id:
            headers["Helicone-User-Id"] = str(user_id)
        if conversation_id:
            headers["Helicone-Session-Id"] = str(conversation_id)
        if session_name:
            headers["Helicone-Session-Name"] = session_name

        if properties:
            for k, v in properties.items():
                headers[f"Helicone-Property-{k}"] = str(v)

        return headers


helicone_service = HeliconeService()
