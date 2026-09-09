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
# Silence litellm's red `Provider List: ...` console banner. register_model()
# resolves every registration through get_llm_provider(); bare deployment slugs
# (e.g. ``compound-mini``, ``qwen/qwen3.8-27b``) have no provider prefix and
# would otherwise spam this print 4x at startup even though registration
# succeeds. Only gates debug prints — never exceptions.
litellm.suppress_debug_info = True

# Register per-token cost info for the deployed provider model slugs. Groq is on
# its free tier (all $0/token) so usage accounting reports accurate USD while
# silencing litellm's `model not in built-in cost map, cost fields default to 0`
# warnings at startup. Provider-prefixed variants all appear in responses/cost
# lookups depending on the call path.
_DEPLOYED_MODEL_COSTS: dict[str, tuple[float, float]] = {
    "openai/groq/compound-mini": (0.0, 0.0),
    "groq/compound-mini": (0.0, 0.0),
    "compound-mini": (0.0, 0.0),
    "openai/qwen/qwen3.8-27b": (0.0, 0.0),
    "groq/qwen/qwen3.8-27b": (0.0, 0.0),
    "qwen/qwen3.8-27b": (0.0, 0.0),
    "openrouter/nvidia/nemotron-3-ultra-550b-a55b:free": (0.0, 0.0),
    "openrouter/inclusionai/ling-3.0-flash-sante:free": (0.0, 0.0),
    "openrouter/google/gemma-4-31b-it:free": (0.0, 0.0),
}
litellm.register_model(
    {
        model_slug: {
            "max_tokens": 8192,
            "input_cost_per_token": input_cost,
            "output_cost_per_token": output_cost,
            "cache_creation_input_token_cost": 0.0,
            "cache_read_input_token_cost": 0.0,
        }
        for model_slug, (input_cost, output_cost) in _DEPLOYED_MODEL_COSTS.items()
    }
)

# Construct model deployment list for LiteLLM Router.
# Primary tier: Groq, live-verified after the VPN was turned off. Groq's current
# catalog uses provider-prefixed ids (e.g. `groq/compound-mini`, `qwen/qwen3.8-27b`)
# and its legacy `llama-*` ids are gone, so deployments use the generic `openai/`
# provider against Groq's OpenAI-compatible base URL (litellm's `groq/` provider
# strips the prefix and would send the wrong model id).
# OpenRouter free-tier models remain as redundant fallbacks (shared-pool 429s).
# Every deployment carries its per-token pricing in ``litellm_params`` so the
# Router's internally-hashed registration for each deployment inherits the same
# $0 free-tier cost fields and emits no ``register_model ... not in built-in cost
# map`` warnings (the hashed ids are opaque and cannot be registered ahead of time).
_CACHE_COST_FIELDS = {
    "input_cost_per_token": 0.0,
    "output_cost_per_token": 0.0,
    "cache_creation_input_token_cost": 0.0,
    "cache_read_input_token_cost": 0.0,
}

model_list = [
    {
        "model_name": "fast_chat",
        "litellm_params": {
            "model": "openai/groq/compound-mini",
            "api_base": "https://api.groq.com/openai/v1",
            "api_key": settings.GROQ_API_KEY,
            "max_tokens": 4096,
            "temperature": 0.7,
            "timeout": 20,
            **_CACHE_COST_FIELDS,
        },
    },
    {
        "model_name": "complex_reasoning",
        "litellm_params": {
            "model": "openai/qwen/qwen3.8-27b",
            "api_base": "https://api.groq.com/openai/v1",
            "api_key": settings.GROQ_API_KEY,
            "max_tokens": 8192,
            "temperature": 0.6,
            "timeout": 25,
            **_CACHE_COST_FIELDS,
        },
    },
    {
        "model_name": "large_context",
        "litellm_params": {
            "model": "openrouter/nvidia/nemotron-3-ultra-550b-a55b:free",
            "api_key": settings.OPENROUTER_API_KEY,
            "max_tokens": 8192,
            "temperature": 0.5,
            "timeout": 30,
            **_CACHE_COST_FIELDS,
        },
    },
    {
        "model_name": "vision_analysis",
        "litellm_params": {
            "model": "openrouter/inclusionai/ling-3.0-flash-sante:free",
            "api_key": settings.OPENROUTER_API_KEY,
            "max_tokens": 4096,
            "temperature": 0.2,
            "timeout": 20,
            **_CACHE_COST_FIELDS,
        },
    },
    # OpenRouter multimodal fallback (Vision-specific capability).
    {
        "model_name": "openrouter_gemma",
        "litellm_params": {
            "model": "openrouter/google/gemma-4-31b-it:free",
            "api_key": settings.OPENROUTER_API_KEY,
            "max_tokens": 4096,
            "temperature": 0.2,
            "timeout": 20,
            **_CACHE_COST_FIELDS,
        },
    },
]

# Model group aliases: bare model names and provider-prefixed strings used
# throughout the codebase, mapped to the Router model_group they belong to.
_MODEL_GROUP_ALIASES = {
    "qwen3.8-27b": "complex_reasoning",
    "qwen/qwen3.8-27b": "complex_reasoning",
    "groq/qwen3.8-27b": "complex_reasoning",
    "compound-mini": "fast_chat",
    "groq/compound-mini": "fast_chat",
    "llama-3.1-8b-instant": "fast_chat",
    "meta-llama/llama-3.1-8b-instruct:free": "fast_chat",
    "minimax-m3": "fast_chat",
    "qwen/qwen-2.5-7b-instruct:free": "fast_chat",
    "google/gemini-flash-1.5:free": "fast_chat",
    "openrouter_llama_8b": "fast_chat",
    "openrouter_gemini_flash": "fast_chat",
    "gpt-oss-120b": "complex_reasoning",
    "openai/gpt-oss-120b": "complex_reasoning",
    "llama-3.3-70b-versatile": "complex_reasoning",
    "openrouter_qwen_7b": "complex_reasoning",
    "openai/gpt-oss-20b": "large_context",
    "mixtral-8x7b-32768": "large_context",
    "gemma-4-26b-a4b-it": "openrouter_gemma",
    "gemma-4-31b-it": "openrouter_gemma",
    "llama-3.2-11b-vision-preview": "openrouter_gemma",
}

# Router model_group → provider/model string for direct litellm calls (tokens, cost).
_GROUP_TO_MODEL = {
    "fast_chat": "groq/compound-mini",
    "complex_reasoning": "qwen/qwen3.8-27b",
    "large_context": "openrouter/nvidia/nemotron-3-ultra-550b-a55b:free",
    "vision_analysis": "openrouter/inclusionai/ling-3.0-flash-sante:free",
    "openrouter_gemma": "openrouter/google/gemma-4-31b-it:free",
}

_GROUP_NAMES = set(_GROUP_TO_MODEL)

# OpenAI-compatible APIs reject LangChain-style role names. Guard at the
# provider boundary so any BaseMessage/dict leakage is normalized.
_ROLE_ALIASES = {"human": "user", "ai": "assistant", "system": "system", "tool": "tool"}


def _normalize_messages(messages: list[Any]) -> list[dict[str, str]]:
    cleaned: list[dict[str, str]] = []
    for m in messages:
        if hasattr(m, "type") and hasattr(m, "content"):
            role = _ROLE_ALIASES.get(m.type, m.type)
            cleaned.append({"role": role, "content": m.content})
        elif isinstance(m, dict):
            role = _ROLE_ALIASES.get(str(m.get("role", "user")), "user")
            content = m.get("content", "")
            cleaned.append({"role": role, "content": content})
        else:
            cleaned.append({"role": "user", "content": str(m)})
    return cleaned


def resolve_model_group(model: str) -> str:
    """Return the Router model_group for any accepted model identifier."""
    if model in _GROUP_NAMES:
        return model
    bare = model.split("/", 1)[-1] if "/" in model else model
    return _MODEL_GROUP_ALIASES.get(bare, model)


def resolve_provider_model(model: str) -> str:
    """Return a provider/model string for direct litellm calls (token/cost math)."""
    if model in _GROUP_NAMES:
        return _GROUP_TO_MODEL[model]
    bare = model.split("/", 1)[-1] if "/" in model else model
    group = _MODEL_GROUP_ALIASES.get(bare)
    if group:
        return _GROUP_TO_MODEL[group]
    return model


# Initialize LiteLLM Router with 3 Specialized Fallback Tiers.
# Fallbacks rotate across distinct OpenRouter free-tier models so a shared-pool
# 429 on one model hands off to another provider instead of ending the stream.
router = Router(
    model_list=model_list,
    fallbacks=[
        {"fast_chat": ["complex_reasoning", "large_context", "openrouter_gemma", "vision_analysis"]},
        {"complex_reasoning": ["fast_chat", "large_context", "openrouter_gemma"]},
        {"large_context": ["complex_reasoning", "fast_chat", "openrouter_gemma"]},
        {"vision_analysis": ["fast_chat", "complex_reasoning"]},
    ],
    context_window_fallbacks=[
        {"fast_chat": ["large_context", "complex_reasoning"]},
        {"complex_reasoning": ["large_context", "fast_chat"]},
    ],
    content_policy_fallbacks=[
        {"complex_reasoning": ["fast_chat"]},
    ],
    cooldown_time=30,
    num_retries=1,
    timeout=25,
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

        group = resolve_model_group(model)
        import time as _time

        _start = _time.perf_counter()
        try:
            # Groq free tier enforces ~1000 output tokens/min (OTPM) per model and
            # rejects any single request whose expected output exceeds it. Keep
            # every completion on the Groq-hosted groups under a safe ceiling so a
            # long internal call can never hard-fail the whole agentic stream.
            if group in ("fast_chat", "complex_reasoning"):
                max_tokens = min(max(max_tokens, 1), 1000)
            response = await self.router.acompletion(
                model=group,
                messages=_normalize_messages(messages),
                temperature=temperature,
                max_tokens=max_tokens,
                extra_headers=extra_headers if extra_headers else None,
            )
        except Exception as exc:
            logger.warning(
                "llm_completion_failed",
                model=group,
                error=str(exc),
                duration_ms=int((_time.perf_counter() - _start) * 1000),
            )
            raise
        _duration_ms = int((_time.perf_counter() - _start) * 1000)

        cost = 0.0
        try:
            cost = completion_cost(completion_response=response) or 0.0
        except Exception:
            # Cost lookup is best-effort: new provider model slugs (e.g.
            # groq/qwen/qwen3.8-27b) may not be in litellm's cost map, and cost
            # accounting must never fail the actual generation.
            logger.warning("completion_cost_lookup_failed", model=getattr(response, "model", ""))
        input_tokens = response.usage.prompt_tokens if response.usage else 0
        output_tokens = response.usage.completion_tokens if response.usage else 0
        logger.info(
            "llm_completion_ok",
            model=group,
            resolved=getattr(response, "model", ""),
            duration_ms=_duration_ms,
            tokens_input=input_tokens,
            tokens_output=output_tokens,
        )

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
        group = resolve_model_group(model)
        import time as _time

        _start = _time.perf_counter()
        try:
            if group in ("fast_chat", "complex_reasoning"):
                max_tokens = min(max(max_tokens, 1), 1000)
            response = await self.router.acompletion(
                model=group,
                messages=_normalize_messages(messages),
                temperature=temperature,
                max_tokens=max_tokens,
                stream=True,
            )
        except Exception as exc:
            logger.warning(
                "llm_stream_start_failed",
                model=group,
                error=str(exc),
                duration_ms=int((_time.perf_counter() - _start) * 1000),
            )
            raise
        logger.info(
            "llm_stream_started",
            model=group,
            resolved=getattr(response, "model", ""),
            first_chunk_latency_ms=int((_time.perf_counter() - _start) * 1000),
        )
        async for chunk in response:
            delta = chunk.choices[0].delta.content or ""
            if delta:
                yield delta

    async def completion(
        self,
        messages: list[dict[str, str]],
        model: str = "complex_reasoning",
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
        model: str = "complex_reasoning",
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

    def count_tokens(self, text: str, model: str = "complex_reasoning") -> int:
        """Estimates token count using litellm encoder."""
        try:
            provider_model = resolve_provider_model(model)
            return len(litellm.encode(model=provider_model, text=text))
        except Exception:
            return max(1, len(text.split()))


litellm_service = LiteLLMService()
ai_client = litellm_service
