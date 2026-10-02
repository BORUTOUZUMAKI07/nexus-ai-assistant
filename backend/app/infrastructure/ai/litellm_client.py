from collections.abc import AsyncGenerator
from typing import Any

import litellm
from backend.app.core.config import settings
from backend.app.core.logging import logger
from backend.app.core.spend import charge_tokens
from backend.app.infrastructure.resilience.guards import CircuitBreaker, InFlightLimiter
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
    "groq/qwen/qwen3.8-27b": (0.0, 0.0),
    "groq/openai/gpt-oss-120b": (0.0, 0.0),
    "openrouter/nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free": (0.0, 0.0),
    "openrouter/liquid/lfm-2.5-2.6b:free": (0.0, 0.0),
    # Router responses report the model WITHOUT the provider prefix
    # (`response.model == "qwen/qwen3.8-27b"`), and that unprefixed string is
    # what `completion_cost` looks up. Registering only the prefixed form makes
    # every Groq call log a `completion_cost_lookup_failed` warning.
    "qwen/qwen3.8-27b": (0.0, 0.0),
    "openai/gpt-oss-120b": (0.0, 0.0),
    "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free": (0.0, 0.0),
    "liquid/lfm-2.5-2.6b:free": (0.0, 0.0),
}
litellm.register_model(
    {
        model_slug: {
            "max_tokens": 4096,
            "input_cost_per_token": input_cost,
            "output_cost_per_token": output_cost,
            "cache_creation_input_token_cost": 0.0,  # nosec B105
            "cache_read_input_token_cost": 0.0,  # nosec B105
        }
        for model_slug, (input_cost, output_cost) in _DEPLOYED_MODEL_COSTS.items()
    }
)

# Every slug below is a LIVE model verified with an actual completion request
# rather than assumed, and the groups deliberately span TWO providers:
#
#   * Groq (`groq/*`) is primary. Its free tier is independent of OpenRouter's
#     and measures ~10x lower time-to-first-token (84-730ms vs 870ms+) on the
#     same kind of request.
#   * OpenRouter `:free` models are the fallback layer. They share one
#     `free-models-per-day` pool, so putting every group on them creates a
#     single point of failure: exhausting that daily cap takes out all traffic.
#     They also lose free tiers without warning (`nex-agi/nex-n2.5-{mini,pro}`
#     started returning HTTP 404 "unavailable for free"), which silently pushed
#     all traffic onto one 2.6B model and made every request take a minute.
#
# Two hard constraints shape the group assignment:
#
#   1. `fast_chat` must return a tight, clean answer for a small output budget.
#      It serves the ARQ route (`_run_arq`, which asks for bare JSON and parses
#      it with `json.loads` under `max_tokens=80`) plus every subagent via
#      settings.FAST_MODEL. Reasoning-tuned models are unusable here: the gpt-oss
#      pair spent all 80 tokens on internal reasoning and returned empty or
#      half-written JSON, which is exactly what produces the retry storm and the
#      `arq_constraint_check_failed_open` fail-open. Note that no caller ever
#      passes a `tools` parameter — `pending_tool_calls` is assembled in Python
#      (see orchestrator/nodes.py), so tool-calling support is NOT the
#      requirement here; JSON discipline under a token cap is.
#   2. Each group points at a DISTINCT model wherever the provider allows it.
#      They once shared two slugs (one already dead), so a single provider outage
#      took out every fallback at the same moment. `large_context` is the one
#      deliberate repeat, documented at its definition.
#
# Re-verify with a live completion request before assuming a slow response is a
# code problem — free-tier churn is the usual culprit.
_CACHE_COST_FIELDS = {
    "input_cost_per_token": 0.0,  # nosec B105
    "output_cost_per_token": 0.0,  # nosec B105
    "cache_creation_input_token_cost": 0.0,  # nosec B105
    "cache_read_input_token_cost": 0.0,  # nosec B105
}

model_list = [
    {
        "model_name": "fast_chat",
        "litellm_params": {
            "model": "groq/qwen/qwen3.8-27b",
            "api_key": settings.GROQ_API_KEY,
            "api_base": "https://api.groq.com/openai/v1",
            "max_tokens": 4096,
            "temperature": 0.7,
            "timeout": 25,
            **_CACHE_COST_FIELDS,
        },
    },
    {
        "model_name": "complex_reasoning",
        "litellm_params": {
            "model": "groq/openai/gpt-oss-120b",
            "api_key": settings.GROQ_API_KEY,
            "api_base": "https://api.groq.com/openai/v1",
            "max_tokens": 4096,
            "temperature": 0.5,
            "timeout": 30,
            **_CACHE_COST_FIELDS,
        },
    },
    {
        "model_name": "large_context",
        "litellm_params": {
            # Deliberately the same model as `fast_chat`. Groq serves only three
            # chat models and the other two (gpt-oss-20b/120b) cannot satisfy the
            # ARQ route: they spend the whole 80-token budget on reasoning and
            # return empty or truncated content. Both groups sit in the ARQ
            # fallback chain, so a synthesis-tuned model here would silently
            # reintroduce the ~6s retry storm. Provider-level redundancy comes
            # from `vision_analysis`/`liquid_fallback` living on OpenRouter.
            "model": "groq/qwen/qwen3.8-27b",
            "api_key": settings.GROQ_API_KEY,
            "api_base": "https://api.groq.com/openai/v1",
            "max_tokens": 8192,
            "temperature": 0.5,
            "timeout": 30,
            **_CACHE_COST_FIELDS,
        },
    },
    {
        "model_name": "vision_analysis",
        "litellm_params": {
            "model": "openrouter/nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free",
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
#
# Split in two because the two halves mean different things and only one of them
# is a promise. A name in `_CURRENT_MODEL_ALIASES` is a model the router actually
# calls, so the group it points at must be the group running that model -- that is
# checked by `test_model_routing_contract.py`, and it is why `qwen3.8-27b`
# resolves to `fast_chat` and not to `complex_reasoning`: both groups serve it,
# but only one of them is its primary, and cost and usage are attributed through
# whichever group this map names. A name in `_LEGACY_MODEL_ALIASES` is a model we
# used to deploy; it is kept so a stale `FAST_MODEL=...` in someone's .env keeps
# resolving instead of falling through the router as an unknown model, and the
# group it points at no longer runs it.
_CURRENT_MODEL_ALIASES = {
    "qwen3.8-27b": "fast_chat",
    "qwen/qwen3.8-27b": "fast_chat",
    "groq/qwen/qwen3.8-27b": "fast_chat",
    "gpt-oss-120b": "complex_reasoning",
    "openai/gpt-oss-120b": "complex_reasoning",
    "groq/openai/gpt-oss-120b": "complex_reasoning",
    "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free": "vision_analysis",
    "liquid/lfm-2.5-2.6b:free": "liquid_fallback",
}

_LEGACY_MODEL_ALIASES = {
    "compound-mini": "fast_chat",
    "groq/compound-mini": "fast_chat",
    "llama-3.1-8b-instant": "fast_chat",
    "meta-llama/llama-3.1-8b-instruct:free": "fast_chat",
    "qwen/qwen-2.5-7b-instruct:free": "fast_chat",
    "google/gemini-flash-1.5:free": "fast_chat",
    "openrouter_llama_8b": "fast_chat",
    "openrouter_gemini_flash": "fast_chat",
    "llama-3.3-70b-versatile": "complex_reasoning",
    "openrouter_qwen_7b": "complex_reasoning",
    "openai/gpt-oss-20b": "large_context",
    "mixtral-8x7b-32768": "large_context",
    # These three used to resolve to an `openrouter_gemma` group that no longer
    # exists in `model_list`. A dangling group name is not a no-op: it makes
    # `resolve_model_group` return something the router has never heard of, so
    # the request leaves the fallback chain entirely instead of falling back.
    # `vision_analysis` is the vision group that replaced it.
    "gemma-4-26b-a4b-it": "vision_analysis",
    "gemma-4-31b-it": "vision_analysis",
    "llama-3.2-11b-vision-preview": "vision_analysis",
}

_MODEL_GROUP_ALIASES = {**_CURRENT_MODEL_ALIASES, **_LEGACY_MODEL_ALIASES}

# Router model_group → provider/model string for direct litellm calls (tokens,
# cost). Must stay in lockstep with the `model` field of each entry in
# `model_list` above — if these drift, usage/cost is attributed to a model that
# was never actually called.
_GROUP_TO_MODEL = {
    "fast_chat": "groq/qwen/qwen3.8-27b",
    "complex_reasoning": "groq/openai/gpt-oss-120b",
    "large_context": "groq/qwen/qwen3.8-27b",
    "vision_analysis": "openrouter/nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free",
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


def _extract_logprobs(response: Any) -> list[dict[str, Any]] | None:
    """Flatten a provider response's per-token logprobs into plain dicts.

    Returns None -- not an empty list -- when the provider did not supply them.
    The distinction is the whole point: an empty list would read as "the model
    produced a token with no alternatives", whereas None means "this provider
    does not do logprobs, or dropped the request", and the typed-decision layer
    treats those differently (it falls back rather than reporting a fabricated
    probability).

    LiteLLM exposes these as objects, but coverage varies by provider and some
    return plain dicts, so both are handled rather than assuming one shape.
    """
    try:
        choices = getattr(response, "choices", None) or []
        if not choices:
            return None
        logprobs = getattr(choices[0], "logprobs", None)
        content = getattr(logprobs, "content", None) if logprobs is not None else None
        if content is None and isinstance(logprobs, dict):
            content = logprobs.get("content")
        if not content:
            return None

        tokens: list[dict[str, Any]] = []
        for entry in content:
            entry_token = getattr(entry, "token", None)
            entry_logprob = getattr(entry, "logprob", None)
            tops = getattr(entry, "top_logprobs", None)
            if entry_token is None and isinstance(entry, dict):
                entry_token = entry.get("token")
                entry_logprob = entry.get("logprob")
                tops = entry.get("top_logprobs")
            if tops is None and isinstance(entry, dict):
                tops = entry.get("top_logprobs")

            alternatives: list[tuple[str, float]] = []
            for alt in tops or []:
                alt_token = getattr(alt, "token", None)
                alt_logprob = getattr(alt, "logprob", None)
                if alt_token is None and isinstance(alt, dict):
                    alt_token = alt.get("token")
                    alt_logprob = alt.get("logprob")
                if alt_token is None or alt_logprob is None:
                    continue
                try:
                    alternatives.append((str(alt_token), float(alt_logprob)))
                except (TypeError, ValueError):
                    # A provider that returns a non-numeric logprob is not
                    # offering a usable distribution; skip it rather than
                    # propagating a value we cannot reason about.
                    continue

            tokens.append(
                {
                    "token": str(entry_token) if entry_token is not None else "",
                    "logprob": float(entry_logprob) if entry_logprob is not None else None,
                    "top": alternatives,
                }
            )
        return tokens or None
    except Exception as exc:  # pragma: no cover - provider shape variation
        logger.warning("llm_logprob_extract_failed", error_type=type(exc).__name__)
        return None


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


# The only TTS engine this codebase ships, and the only container edge-tts can
# emit. Both were declared as settings (TTS_MODEL, TTS_FORMAT) and read by
# nothing, so pointing them at anything else was silently a no-op.
_TTS_ENGINES = {"edge-tts"}
_TTS_MEDIA_TYPES = {"mp3": "audio/mpeg"}


def tts_media_type() -> str:
    """Validate the configured TTS engine/format and return the HTTP media type.

    edge-tts streams a single fixed encoding (24 kHz, 48 kbit/s, mono MP3) and
    offers no model selection, so these two settings cannot in fact steer the
    output. What they can do is be checked: rather than accept a value and
    quietly ignore it, an unsupported engine or format is rejected with a
    message naming the supported ones. That is the difference between a
    misconfiguration that is visible and one that is not.
    """
    engine = (settings.TTS_MODEL or "").strip().lower()
    fmt = (settings.TTS_FORMAT or "").strip().lower().lstrip(".")

    if engine not in _TTS_ENGINES:
        raise ValueError(
            f"TTS_MODEL={settings.TTS_MODEL!r} is not a supported TTS engine. "
            f"Supported: {', '.join(sorted(_TTS_ENGINES))}."
        )
    if fmt not in _TTS_MEDIA_TYPES:
        raise ValueError(
            f"TTS_FORMAT={settings.TTS_FORMAT!r} is not a format edge-tts can "
            f"produce. Supported: {', '.join(sorted(_TTS_MEDIA_TYPES))}."
        )
    return _TTS_MEDIA_TYPES[fmt]


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

# Resilience guards (HLD: circuit breaker + bulkhead in-flight cap), keyed per
# model group. A dead provider trips its group's breaker so later requests skip
# that group immediately instead of grinding through every fallback; the
# in-flight cap keeps free-tier provider quotas from being exhausted by a burst
# of concurrent agent work. Both are process-local and reset on deploy.
_llm_breaker = CircuitBreaker(failure_threshold=5, open_timeout_seconds=60.0)
_llm_in_flight = InFlightLimiter(max_in_flight=8)


class LiteLLMService:
    def __init__(
        self,
        llm_router: Router = router,
        circuit_breaker: CircuitBreaker | None = None,
        in_flight_limiter: InFlightLimiter | None = None,
    ):
        self.router = llm_router
        self._breaker = circuit_breaker if circuit_breaker is not None else _llm_breaker
        self._in_flight = in_flight_limiter if in_flight_limiter is not None else _llm_in_flight

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
        response_format: dict | None = None,
        logprobs: bool = False,
        top_logprobs: int = 0,
    ) -> dict[str, Any]:
        """Executes non-streaming completion with automatic fallbacks and cost tracking.

        ``response_format`` passes provider-level structured-output requests
        (e.g. ``{"type": "json_object"}``) through to the router. Standard
        APIs drop it on providers that do not support it (``drop_params=True``),
        so callers must keep retry-with-repair / heuristic fallbacks afterwards.

        ``logprobs`` asks the provider for per-token log-probabilities and, when
        the provider returns them, surfaces them under ``"logprobs"`` in the
        result. This exists for the typed-decision layer in
        ``services/decision.py``, which needs the real score of each candidate
        answer rather than a number a chat model made up when asked how sure it
        was. It defaults off and is forwarded only when requested, so no
        existing call site changes behaviour, pays for it, or depends on a
        provider that cannot supply logprobs. Providers that ignore the request
        return nothing here, which the caller must treat as "unavailable"
        rather than "confident".
        """
        extra_headers: dict[str, str] = {}
        if enable_caching:
            extra_headers["cache-control"] = "ephemeral"

        group = resolve_model_group(model)
        import time as _time

        # Provider-specific structured-output and logprob requests are forwarded
        # as loose kwargs, which is how `drop_params=True` lets a provider that
        # cannot do them ignore the request instead of failing the call.
        _provider_kwargs: dict[str, Any] = dict(response_format) if response_format else {}
        if logprobs:
            _provider_kwargs["logprobs"] = True
            # Providers cap this and drop the request if it is out of range;
            # 20 is the widest commonly accepted window.
            _provider_kwargs["top_logprobs"] = top_logprobs or 20

        _start = _time.perf_counter()
        # Groq free tier enforces ~1000 output tokens/min (OTPM) per model and
        # rejects any single request whose expected output exceeds it. Keep
        # every completion on the Groq-hosted groups under a safe ceiling so a
        # long internal call can never hard-fail the whole agentic stream.

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

            # Circuit breaker: skip the group entirely while it is OPEN.
            if not await self._breaker.allow(_attempt_group):
                logger.warning("llm_circuit_open_skip", model=_attempt_group)
                last_exc = last_exc or RuntimeError(
                    f"Provider circuit open for model group '{_attempt_group}'"
                )
                continue

            try:
                # Bulkhead: cap concurrent in-flight calls per provider group.
                async with self._in_flight.acquire(_attempt_group):
                    response = await self.router.acompletion(
                        model=_attempt_group,
                        messages=_normalize_messages(messages),
                        temperature=temperature,
                        max_tokens=_eff_tokens,
                        extra_headers=extra_headers if extra_headers else None,
                        **_provider_kwargs,
                    )
            except Exception as exc:
                await self._breaker.record_failure(_attempt_group)
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
                await self._breaker.record_success(_attempt_group)
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
        # Feed the per-run spend meter. Done here, at the only place that
        # already knows a call's token usage, so every model call in the
        # process is counted without any call site having to remember to --
        # including the planner, router, critic and subagents, and any call
        # site added later. A hand-maintained counter in each node would
        # undercount precisely when a new node is added and nobody updates it.
        charge_tokens(input_tokens + output_tokens)
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
            # None unless asked for AND the provider supplied them. Always
            # present so callers do not need a `.get()` guard that silently
            # treats a missing key as an absent feature.
            "logprobs": _extract_logprobs(response) if logprobs else None,
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

        # Circuit breaker: refuse to start a stream against an OPEN group.
        if not await self._breaker.allow(group):
            logger.warning("llm_stream_circuit_open", model=group)
            raise RuntimeError(f"Provider circuit open for model group '{group}'")

        async with self._in_flight.acquire(group):
            try:
                response = await self.router.acompletion(
                    model=group,
                    messages=_normalize_messages(messages),
                    temperature=temperature,
                    max_tokens=effective_max_tokens,
                    stream=True,
                )
            except Exception as exc:
                await self._breaker.record_failure(group)
                logger.warning(
                    "llm_stream_start_failed",
                    model=group,
                    error=str(exc),
                    duration_ms=int((_time.perf_counter() - _start) * 1000),
                )
                raise
            await self._breaker.record_success(group)
            logger.info(
                "llm_stream_started",
                model=group,
                resolved=getattr(response, "model", ""),
                first_chunk_latency_ms=int((_time.perf_counter() - _start) * 1000),
            )
            try:
                _streamed_chars = 0
                async for chunk in response:
                    # Guard: streaming chunks can have delta.content = None between
                    # thinking tokens — skip silently rather than yielding "None" strings.
                    delta = (chunk.choices[0].delta.content if chunk.choices else None) or ""
                    if delta:
                        _streamed_chars += len(delta)
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
                # Charge the spend meter for the stream. This is an ESTIMATE, not
                # reported usage: litellm only exposes usage on a streaming call if
                # the request sets stream_options={"include_usage": True}, and
                # adding that changes the request shape for every provider in the
                # router -- not a change to make blind, on free tiers whose exact
                # accounting is what pays the bill. Characters/4 is the standard
                # approximation and is deliberately cheap, because the point is
                # to keep the final answer visible in the run total rather than to
                # bill it. The request side is already hard-capped by
                # `effective_max_tokens`.
                charge_tokens((_streamed_chars + 3) // 4)

    async def completion(
        self,
        messages: list[dict[str, str]],
        model: str = "complex_reasoning",
        temperature: float = 0.7,
        max_tokens: int = 4096,
        response_format: dict | None = None,
    ) -> str:
        """Convenience method returning text response string."""
        res = await self.complete(
            messages=messages,
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            response_format=response_format,
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

    async def synthesize_speech(
        self,
        text: str,
        voice: str | None = None,
        model: str | None = None,
    ) -> bytes:
        """
        Text-to-speech via Microsoft Edge neural voices (edge-tts) — free and
        key-less: no provider key or billing is ever required. `voice` is an
        edge-tts voice id (default settings.TTS_VOICE). The `model` argument
        is accepted for API compatibility and ignored; the response is still
        a raw MP3 byte string so the /audio/speech endpoint contract holds.

        The configured TTS engine and format are validated first, so a bad
        TTS_MODEL/TTS_FORMAT surfaces here instead of being ignored.
        """
        import edge_tts

        tts_media_type()
        voice_id = voice or settings.TTS_VOICE
        try:
            communicate = edge_tts.Communicate(text, voice=voice_id)
            chunks: list[bytes] = []
            async for chunk in communicate.stream():
                if chunk.get("type") == "audio":
                    chunks.append(chunk["data"])
            audio = b"".join(chunks)
            if not audio:
                raise RuntimeError("Edge TTS returned no audio data")
            return audio
        except Exception as exc:
            logger.error("tts_synthesis_failed", error=str(exc))
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
