"""
Structured Output Service using Instructor + Pydantic.
Provides guaranteed type-safe responses, automated validation error retries,
and partial JSON streaming capabilities.
"""
from collections.abc import AsyncGenerator
from typing import Any, TypeVar

import instructor
import structlog
from backend.app.core.config import settings
from backend.app.core.spend import charge_tokens
from backend.app.infrastructure.ai.litellm_client import (
    resolve_model_group,
    router,
)
from pydantic import BaseModel

logger = structlog.get_logger(__name__)

T = TypeVar("T", bound=BaseModel)


async def _metered_router_acompletion(*args: Any, **kwargs: Any) -> Any:
    """``router.acompletion`` with the per-run spend meter fed.

    Instructor is pointed at *this* rather than the bare router. The raw router
    bypassed ``charge_tokens`` (which lives in ``LiteLLMService.complete``), so
    every structured call -- including the artifact classifier -- was invisible
    to ``AGENT_LOOP_TOKEN_BUDGET``. P1 #12.
    """
    response = await router.acompletion(*args, **kwargs)
    try:
        usage = getattr(response, "usage", None)
        if usage:
            charge_tokens(
                (getattr(usage, "prompt_tokens", 0) or 0)
                + (getattr(usage, "completion_tokens", 0) or 0)
            )
    except Exception as exc:
        logger.warning("structured_output_metering_failed", error=str(exc))
    return response


class StructuredOutputService:
    """
    Service wrapper around Instructor for type-safe LLM outputs.
    Integrates with LiteLLM for multi-provider routing and fallbacks.
    """

    def __init__(self):
        # Patch the LiteLLM Router (not raw litellm) with instructor in async
        # mode so model_group names and per-deployment API keys apply here too.
        # Wrapped so the spend meter sees these calls (P1 #12).
        self.client = instructor.from_litellm(_metered_router_acompletion)

    async def generate_structured(
        self,
        response_model: type[T],
        messages: list[dict[str, str]],
        model: str | None = None,
        temperature: float = 0.2,
        max_retries: int = 3,
        **kwargs: Any,
    ) -> T:
        """
        Extract strongly typed structured data from messages.
        If validation fails, Instructor automatically re-prompts the model
        with the validation error up to `max_retries` times.
        """
        target_model = resolve_model_group(model or settings.DEFAULT_MODEL)
        logger.info(
            "generating_structured_output",
            model=target_model,
            response_model=response_model.__name__,
            max_retries=max_retries,
        )

        try:
            result = await self.client.chat.completions.create(
                model=target_model,
                response_model=response_model,
                messages=messages,
                temperature=temperature,
                max_retries=max_retries,
                **kwargs,
            )
            return result
        except Exception as exc:
            logger.error("structured_output_generation_failed", error=str(exc), model=target_model)
            raise

    async def stream_partial(
        self,
        response_model: type[T],
        messages: list[dict[str, str]],
        model: str | None = None,
        temperature: float = 0.2,
        **kwargs: Any,
    ) -> AsyncGenerator[T, None]:
        """
        Stream partial structured JSON chunks as they arrive from the LLM,
        validating incomplete Pydantic objects progressively.
        """
        target_model = resolve_model_group(model or settings.DEFAULT_MODEL)
        logger.info("streaming_partial_structured_output", model=target_model, response_model=response_model.__name__)

        try:
            response_stream = await self.client.chat.completions.create_partial(
                model=target_model,
                response_model=response_model,
                messages=messages,
                temperature=temperature,
                stream=True,
                **kwargs,
            )
            async for partial_obj in response_stream:
                yield partial_obj
        except Exception as exc:
            logger.error("stream_partial_structured_output_failed", error=str(exc))
            raise


structured_service = StructuredOutputService()
