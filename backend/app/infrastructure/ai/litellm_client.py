from collections.abc import AsyncGenerator
from typing import Any

import litellm
from backend.app.core.config import settings
from backend.app.core.logging import logger
from backend.app.services.observability.helicone import helicone_service
from litellm import Router, completion_cost

# Configure litellm global settings
litellm.drop_params = True
litellm.telemetry = False

# Construct model deployment list for LiteLLM Router
model_list = [
    # Primary Groq Models (Ultra-fast, Free Tier)
    {
        "model_name": "fast_chat",
        "litellm_params": {
            "model": "groq/llama-3.1-8b-instant",
            "api_key": settings.GROQ_API_KEY,
            "max_tokens": 4096,
            "temperature": 0.7,
        },
    },
    {
        "model_name": "complex_reasoning",
        "litellm_params": {
            "model": "groq/llama-3.3-70b-versatile",
            "api_key": settings.GROQ_API_KEY,
            "max_tokens": 4096,
            "temperature": 0.6,
        },
    },
    {
        "model_name": "large_context",
        "litellm_params": {
            "model": "groq/mixtral-8x7b-32768",
            "api_key": settings.GROQ_API_KEY,
            "max_tokens": 4096,
            "temperature": 0.5,
        },
    },
    # OpenRouter Fallback Models (Free Tier Failover)
    {
        "model_name": "openrouter_llama_8b",
        "litellm_params": {
            "model": "openrouter/meta-llama/llama-3.1-8b-instruct:free",
            "api_key": settings.OPENROUTER_API_KEY,
        },
    },
    {
        "model_name": "openrouter_qwen_7b",
        "litellm_params": {
            "model": "openrouter/qwen/qwen-2.5-7b-instruct:free",
            "api_key": settings.OPENROUTER_API_KEY,
        },
    },
    {
        "model_name": "vision_analysis",
        "litellm_params": {
            "model": "groq/llama-3.2-11b-vision-preview",
            "api_key": settings.GROQ_API_KEY,
            "max_tokens": 4096,
            "temperature": 0.2,
        },
    },
    {
        "model_name": "openrouter_gemini_flash",
        "litellm_params": {
            "model": "openrouter/google/gemini-flash-1.5:free",
            "api_key": settings.OPENROUTER_API_KEY,
        },
    },
]

# Initialize LiteLLM Router with 3 Specialized Fallback Tiers
router = Router(
    model_list=model_list,
    fallbacks=[
        {"fast_chat": ["openrouter_llama_8b"]},
        {"complex_reasoning": ["openrouter_qwen_7b", "openrouter_gemini_flash"]},
        {"vision_analysis": ["openrouter_gemini_flash"]},
    ],
    context_window_fallbacks=[
        {"fast_chat": ["large_context", "openrouter_gemini_flash"]},
        {"complex_reasoning": ["large_context", "openrouter_gemini_flash"]},
    ],
    content_policy_fallbacks=[
        {"complex_reasoning": ["openrouter_qwen_7b"]},
    ],
    cooldown_time=60,
    num_retries=2,
    timeout=30,
)


class LiteLLMService:
    def __init__(self, llm_router: Router = router):
        self.router = llm_router

    async def complete(
        self,
        messages: list[dict[str, str]],
        model: str = "complex_reasoning",
        temperature: float = 0.7,
        max_tokens: int = 4096,
        user_id: str | None = None,
        conversation_id: str | None = None,
        session_name: str | None = None,
        enable_caching: bool = True,
    ) -> dict[str, Any]:
        """Executes non-streaming completion with automatic fallbacks and cost tracking."""
        extra_headers: dict[str, str] = {}
        if enable_caching:
            extra_headers["cache-control"] = "ephemeral"
        extra_headers.update(
            helicone_service.get_headers(
                user_id=user_id,
                conversation_id=conversation_id,
                session_name=session_name,
            )
        )

        response = await self.router.acompletion(
            model=model,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            extra_headers=extra_headers if extra_headers else None,
        )

        cost = completion_cost(completion_response=response)
        input_tokens = response.usage.prompt_tokens if response.usage else 0
        output_tokens = response.usage.completion_tokens if response.usage else 0

        return {
            "content": response.choices[0].message.content or "",
            "model": response.model,
            "tokens_input": input_tokens,
            "tokens_output": output_tokens,
            "cost_usd": cost or 0.0,
        }

    async def astream(
        self,
        messages: list[dict[str, str]],
        model: str = "complex_reasoning",
        temperature: float = 0.7,
        max_tokens: int = 4096,
        user_id: str | None = None,
    ) -> AsyncGenerator[str, None]:
        """Streams token-by-token chunks over async generator."""
        response = await self.router.acompletion(
            model=model,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            stream=True,
        )
        async for chunk in response:
            delta = chunk.choices[0].delta.content or ""
            if delta:
                yield delta

    async def completion(
        self,
        messages: list[dict[str, str]],
        model: str = "llama-3.3-70b-versatile",
        temperature: float = 0.7,
        max_tokens: int = 4096,
    ) -> str:
        """Convenience method returning text response string."""
        res = await self.complete(
            messages=messages,
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        return res.get("content", "")

    async def stream_completion(
        self,
        messages: list[dict[str, str]],
        model: str = "llama-3.3-70b-versatile",
        temperature: float = 0.7,
        max_tokens: int = 4096,
    ) -> AsyncGenerator[str, None]:
        """Convenience stream method yielding text tokens."""
        async for chunk in self.astream(
            messages=messages,
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
        ):
            yield chunk

    async def transcribe_audio(
        self,
        audio_file: Any,
        model: str = "groq/whisper-large-v3",
    ) -> str:
        """Transcribes audio files into text using Groq Whisper."""
        try:
            res = await litellm.atranscription(
                model=model,
                file=audio_file,
                api_key=settings.GROQ_API_KEY,
            )
            return res.text if hasattr(res, "text") else str(res)
        except Exception as exc:
            logger.error("audio_transcription_failed", error=str(exc))
            raise

    def count_tokens(self, text: str, model: str = "llama-3.3-70b-versatile") -> int:
        """Estimates token count using litellm encoder."""
        try:
            return len(litellm.encode(model=model, text=text))
        except Exception:
            return max(1, len(text.split()))


litellm_service = LiteLLMService()
ai_client = litellm_service
