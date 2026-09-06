"""
Cost Tracking Service.
Computes token expense based on provider pricing tables and logs usage.
"""
from datetime import datetime
from typing import Any
from uuid import UUID

import structlog
from backend.app.domain.usage.models import CostLog, UsageLog
from backend.app.domain.usage.repository import UsageRepository
from sqlmodel.ext.asyncio.session import AsyncSession

logger = structlog.get_logger(__name__)

# Price per million tokens (USD)
MODEL_PRICING: dict[str, dict[str, float]] = {
    "llama-3.3-70b-versatile": {"input": 0.59, "output": 0.79},
    "llama-3.1-8b-instant": {"input": 0.05, "output": 0.08},
    "mixtral-8x7b-32768": {"input": 0.24, "output": 0.24},
    "claude-3-5-sonnet": {"input": 3.00, "output": 15.00},
    "gpt-4o": {"input": 2.50, "output": 10.00},
    "gpt-4o-mini": {"input": 0.15, "output": 0.60},
    "default": {"input": 0.50, "output": 0.50},
}


class CostTrackingService:
    """Calculates LLM expenses and records per-turn cost analytics."""

    @staticmethod
    def _price_model(model: str) -> str:
        """Strip any provider prefix (e.g. 'groq/llama-3.3-70b-versatile' → 'llama-3.3-70b-versatile')."""
        return model.split("/", 1)[-1] if model and "/" in model else model

    @classmethod
    def calculate_cost(
        cls, model: str, prompt_tokens: int, completion_tokens: int
    ) -> float:
        pricing = MODEL_PRICING.get(cls._price_model(model), MODEL_PRICING["default"])
        input_cost = (prompt_tokens / 1_000_000) * pricing["input"]
        output_cost = (completion_tokens / 1_000_000) * pricing["output"]
        return round(input_cost + output_cost, 6)

    @classmethod
    async def record_cost_log(
        cls,
        session: AsyncSession,
        user_id: UUID,
        model: str,
        provider: str,
        prompt_tokens: int,
        completion_tokens: int,
    ) -> CostLog:
        """
        Persists the monthly aggregate CostLog row only (does NOT create a UsageLog).
        Call alongside existing usage logging so the billing-period rollup stays in sync.
        """
        price_model = cls._price_model(model)
        pricing = MODEL_PRICING.get(price_model, MODEL_PRICING["default"])
        input_cost = (prompt_tokens / 1_000_000) * pricing["input"]
        output_cost = (completion_tokens / 1_000_000) * pricing["output"]
        cost = round(input_cost + output_cost, 6)

        current_period = datetime.utcnow().strftime("%Y-%m")
        cost_entry = CostLog(
            user_id=user_id,
            provider=provider,
            model=model,
            input_cost=input_cost,
            output_cost=output_cost,
            total_cost=cost,
            billing_period=current_period,
        )
        created = await UsageRepository(session).create(cost_entry)
        logger.info("monthly_cost_log_recorded", user_id=str(user_id), model=model, total_cost=cost, period=current_period)
        return created

    @classmethod
    async def record_usage(
        cls,
        session: AsyncSession,
        user_id: UUID,
        model: str,
        provider: str,
        prompt_tokens: int,
        completion_tokens: int,
        latency_ms: int,
        conversation_id: UUID | None = None,
        message_id: UUID | None = None,
        cached_tokens: int = 0,
        status: str = "success",
        error_message: str | None = None,
        metadata_json: dict[str, Any] | None = None,
    ) -> UsageLog:
        cost = cls.calculate_cost(model, prompt_tokens, completion_tokens)
        usage_repo = UsageRepository(session)

        log = UsageLog(
            user_id=user_id,
            conversation_id=conversation_id,
            message_id=message_id,
            model=model,
            provider=provider,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=prompt_tokens + completion_tokens,
            cached_tokens=cached_tokens,
            latency_ms=latency_ms,
            cost_usd=cost,
            status=status,
            error_message=error_message,
            metadata_json=metadata_json,
        )
        created_log = await usage_repo.create(log)

        # Aggregate monthly cost log
        current_period = datetime.utcnow().strftime("%Y-%m")
        price_model = cls._price_model(model)
        pricing = MODEL_PRICING.get(price_model, MODEL_PRICING["default"])
        cost_entry = CostLog(
            user_id=user_id,
            provider=provider,
            model=model,
            input_cost=(prompt_tokens / 1_000_000) * pricing["input"],
            output_cost=(completion_tokens / 1_000_000) * pricing["output"],
            total_cost=cost,
            billing_period=current_period,
        )
        await usage_repo.create(cost_entry)

        logger.info(
            "llm_usage_recorded",
            user_id=str(user_id),
            model=model,
            total_tokens=prompt_tokens + completion_tokens,
            cost_usd=cost,
        )
        return created_log


cost_tracking_service = CostTrackingService()
