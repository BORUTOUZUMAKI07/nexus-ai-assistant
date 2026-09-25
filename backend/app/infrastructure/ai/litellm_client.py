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
litellm.set_verbose = False
# Silence litellm's red `Provider List: ...` console banner. register_model()
# resolves every registration through get_llm_provider(); bare deployment slugs
# (e.g. ``compound-mini``, ``groq/compound``) have no provider prefix and
# would otherwise spam this print 4x at startup even though registration
# succeeds. Only gates debug prints — never exceptions.
litellm.suppress_debug_info = True

# Register per-token cost info for the deployed provider model slugs. Groq is on
# its free tier (all $0/token) so usage accounting reports accurate USD while
# silencing litellm's `model not in built-in cost map, cost fields default to 0`
# warnings at startup. Provider-prefixed variants all appear in responses/cost
# lookups depending on the call path.
_DEPLOYED_MODEL_COSTS: dict[str, tuple[float, float]] = {
    "openrouter/nex-agi/nex-n2.5-mini:free": (0.0, 0.0),
    "openrouter/nex-agi/nex-n2.5-pro:free": (0.0, 0.0),
    "openrouter/liquid/lfm-2.5-2.6b:free": (0.0, 0.0),
    "openrouter/nvidia/nemotron-3-ultra-550b-a55b:free": (0.0, 0.0),
    "openrouter/inclusionai/ling-3.0-flash-sante:free": (0.0, 0.0),
    "openrouter/google/gemma-4-31b-it:free": (0.0, 0.0),
}
litellm.register_model(
    {
        model_slug: {
            "max_tokens": 4096,
            "input_cost_per_token": input_cost,
            "output_cost_per_token": output_cost,
            "cache_creation_input_token_cost": 0.0,
            "cache_read_input_token_cost": 0.0,
        }
        for model_slug, (input_cost, output_cost) in _DEPLOYED_MODEL_COSTS.items()
    }
)

# Construct model deployment list for LiteLLM Router.
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
            "model": "openrouter/nex-agi/nex-n2.5-mini:free",
            "api_key": settings.OPENROUTER_API_KEY,
            "max_tokens": 4096,
            "temperature": 0.7,
            "timeout": 25,
            **_CACHE_COST_FIELDS,
        },
    },
    {
        "model_name": "complex_reasoning",
        "litellm_params": {
            "model": "openrouter/nex-agi/nex-n2.5-pro:free",
            "api_key": settings.OPENROUTER_API_KEY,
            "max_tokens": 4096,
            "temperature": 0.5,
            "timeout": 30,
            **_CACHE_COST_FIELDS,
        },
    },
    {
        "model_name": "large_context",
        "litellm_params": {
            "model": "openrouter/nex-agi/nex-n2.5-pro:free",
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
            "model": "openrouter/nex-agi/nex-n2.5-mini:free",
            "api_key": settings.OPENROUTER_API_KEY,
            "max_tokens": 4096,
            "temperature": 0.2,
            "timeout": 25,
            **_CACHE_COST_FIELDS,
        },
    },
    {
        "model_name": "liquid_fallback",
        "litellm_params": {
            "model": "openrouter/liquid/lfm-2.5-2.6b:free",
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
    "fast_chat": "openrouter/nex-agi/nex-n2.5-mini:free",
    "complex_reasoning": "openrouter/nex-agi/nex-n2.5-pro:free",
    "large_context": "openrouter/nex-agi/nex-n2.5-pro:free",
    "vision_analysis": "openrouter/nex-agi/nex-n2.5-mini:free",
    "liquid_fallback": "openrouter/liquid/lfm-2.5-2.6b:free",
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
router = Router(
    model_list=model_list,
    fallbacks=[
        {"fast_chat": ["complex_reasoning", "large_context", "liquid_fallback", "vision_analysis"]},
        {"complex_reasoning": ["fast_chat", "large_context", "liquid_fallback"]},
        {"large_context": ["complex_reasoning", "fast_chat", "liquid_fallback"]},
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
        # Groq free tier enforces ~1000 output tokens/min (OTPM) per model and
        # rejects any single request whose expected output exceeds it. Keep
        # every completion on the Groq-hosted groups under a safe ceiling so a
        # long internal call can never hard-fail the whole agentic stream.
        effective_max_tokens = min(max(max_tokens, 1), 1000) if group in ("fast_chat", "complex_reasoning") else max_tokens

        # Fallback chain: if primary group returns empty content (no text + no tool calls),
        # retry once with the next available group before raising. This guards against
        # the "model output must contain either output text or tool calls" error that
        # some Groq-hosted thinking models emit when they exhaust their token budget.
        _fallback_groups = ["large_context", "liquid_fallback", "vision_analysis"]
        _attempt_groups = [group] + [g for g in _fallback_groups if g != group]

        response = None
        last_exc: Exception | None = None
        for _attempt_group in _attempt_groups:
            _eff_tokens = min(max(max_tokens, 1), 1000) if _attempt_group in ("fast_chat", "complex_reasoning") else max_tokens
            try:
                response = await self.router.acompletion(
                    model=_attempt_group,
                    messages=_normalize_messages(messages),
                    temperature=temperature,
                    max_tokens=_eff_tokens,
                    extra_headers=extra_headers if extra_headers else None,
                )
            except Exception as exc:
                last_exc = exc
                logger.warning(
                    "llm_completion_failed",
                    model=_attempt_group,
                    error=str(exc),
                    duration_ms=int((_time.perf_counter() - _start) * 1000),
                )
                continue

            # Guard: some reasoning models (qwen thinking mode) return content=None
            # with no tool calls — this causes the litellm "model output must contain
            # either output text or tool calls" validation error.  Detect early and
            # retry with the next fallback group before propagating.
            _msg = response.choices[0].message if response.choices else None
            _content = getattr(_msg, "content", None) if _msg else None
            _tool_calls = getattr(_msg, "tool_calls", None) if _msg else None
            if _content or _tool_calls:
                # Valid response — stop retrying.
                break

            logger.warning(
                "llm_empty_output_retrying",
                model=_attempt_group,
                attempt_group=_attempt_group,
            )
            response = None  # Mark as invalid so we try next group

        if response is None:
            if last_exc:
                raise last_exc
            raise RuntimeError("All model groups returned empty output with no tool calls.")

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

        # Safe content extraction: prefer text content; fall back to tool call
        # serialization; final fallback is empty string (never None).
        _final_msg = response.choices[0].message
        _raw_content = getattr(_final_msg, "content", None) or ""
        return {
            "content": _raw_content,
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
        effective_max_tokens = min(max(max_tokens, 1), 1000) if group in ("fast_chat", "complex_reasoning") else max_tokens
        try:
            response = await self.router.acompletion(
                model=group,
                messages=_normalize_messages(messages),
                temperature=temperature,
                max_tokens=effective_max_tokens,
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
        try:
            async for chunk in response:
                # Guard: streaming chunks can have delta.content = None between
                # thinking tokens — skip silently rather than yielding "None" strings.
                delta = (chunk.choices[0].delta.content if chunk.choices else None) or ""
                if delta:
                    yield delta
        finally:
            # Always release the upstream SSE connection, including when the
            # consumer generator is closed early (client disconnect) or a
            # provider error interrupts the loop. Leaking it stalls the provider
            # HTTP pool and can wedge later runs.
            try:
                await response.aclose()
            except Exception as exc:
                logger.warning("llm_stream_aclose_failed", model=group, error=str(exc))

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
