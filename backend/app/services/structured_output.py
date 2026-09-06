"""
Structured Output Service using Instructor + Pydantic.
Provides guaranteed type-safe responses, automated validation error retries,
and partial JSON streaming capabilities.
"""
from collections.abc import AsyncGenerator
from typing import Any, TypeVar

import instructor
import litellm
import structlog
from backend.app.core.config import settings
from pydantic import BaseModel

logger = structlog.get_logger(__name__)

T = TypeVar("T", bound=BaseModel)


class StructuredOutputService:
    """
    Service wrapper around Instructor for type-safe LLM outputs.
    Integrates with LiteLLM for multi-provider routing and fallbacks.
    """

    def __init__(self):
        # Patch litellm with instructor in async mode
        self.client = instructor.from_litellm(litellm.acompletion)

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
        target_model = model or settings.DEFAULT_MODEL
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
        target_model = model or settings.DEFAULT_MODEL
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
