"""
Context Compiler and Attention Budget Compactor.
Implements sliding-window conversation compaction, token budgeting,
and summary preservation to prevent context overflow.
"""
from typing import Any

import structlog
from backend.app.domain.conversation.models import Message
from backend.app.infrastructure.ai.litellm_client import ai_client

logger = structlog.get_logger(__name__)

# Default token context limits per model
MODEL_CONTEXT_LIMITS = {
    "llama-3.3-70b-versatile": 128000,
    "llama-3.1-8b-instant": 128000,
    "deepseek-r1-distill-llama-70b": 128000,
    "default": 32768,
}


def _normalize_model(model: str) -> str:
    """Strip a litellm provider prefix so lookups hit MODEL_CONTEXT_LIMITS.

    Callers pass `settings.DEFAULT_MODEL` = ``groq/llama-3.3-70b-versatile``,
    which never matches the unprefixed keys above — every lookup silently fell
    back to ``default: 32768`` and compaction kicked in ~4x too early.
    """
    return model.rsplit("/", 1)[-1] if "/" in model else model


class ContextCompiler:
    """
    Manages the conversational context window and compaction.
    When message history exceeds the budget threshold (e.g. 80%),
    older messages are summarized into an episodic summary node while
    keeping the most recent turns intact.
    """

    def __init__(self, target_budget_ratio: float = 0.75, reserve_completion_tokens: int = 4096):
        self.target_budget_ratio = target_budget_ratio
        self.reserve_completion_tokens = reserve_completion_tokens
        # Injected so tests can drive summarisation without a live provider; the
        # module-level singleton below keeps the production default.
        self.ai_client = ai_client

    def estimate_tokens(self, text: str, model: str = "llama-3.3-70b-versatile") -> int:
        return ai_client.count_tokens(text, model)

    def get_max_context(self, model: str) -> int:
        return MODEL_CONTEXT_LIMITS.get(_normalize_model(model), MODEL_CONTEXT_LIMITS["default"])

    async def summarize_messages(self, messages_to_compress: list[Message], model: str) -> str:
        """
        Compress older messages into a concise factual summary.
        """
        conversation_transcript = "\n".join(
            [f"{m.role.upper()}: {m.content}" for m in messages_to_compress]
        )

        prompt = (
            "Summarize the key facts, user preferences, decisions, and outcomes from this conversation snippet. "
            "Keep it compact, dense, and factual.\n\n"
            f"{conversation_transcript}"
        )

        summary = await ai_client.completion(
            messages=[
                {"role": "system", "content": "You are a concise conversation summarizer."},
                {"role": "user", "content": prompt},
            ],
            model=model,
            max_tokens=500,
            temperature=0.2,
        )
        return summary.strip()

    async def compile_context(
        self,
        messages: list[Message],
        system_prompt: str,
        model: str = "llama-3.3-70b-versatile",
        max_recent_turns: int = 6,
    ) -> list[dict[str, str]]:
        """
        Compiles the messages list into a final payload suitable for LLM execution.
        If the token limit is exceeded, automatically compacts older messages.
        """
        max_context = self.get_max_context(model)
        usable_budget = int(max_context * self.target_budget_ratio) - self.reserve_completion_tokens

        system_tokens = self.estimate_tokens(system_prompt, model)
        remaining_budget = usable_budget - system_tokens

        formatted_messages: list[dict[str, str]] = [{"role": "system", "content": system_prompt}]

        if not messages:
            return formatted_messages

        # Calculate tokens from newest to oldest
        total_msg_tokens = 0
        kept_messages: list[Message] = []
        messages_to_compress: list[Message] = []

        for msg in reversed(messages):
            msg_tokens = msg.total_tokens or self.estimate_tokens(msg.content, model)
            if total_msg_tokens + msg_tokens <= remaining_budget or len(kept_messages) < max_recent_turns:
                kept_messages.insert(0, msg)
                total_msg_tokens += msg_tokens
            else:
                messages_to_compress.insert(0, msg)

        # If we have older messages that overflowed the budget, generate an episodic summary
        if messages_to_compress:
            logger.info("compacting_conversation_context", compressed_count=len(messages_to_compress), kept_count=len(kept_messages))
            summary = await self.summarize_messages(messages_to_compress, model=model)
            formatted_messages.append({
                "role": "system",
                "content": f"[Previous Conversation Summary]: {summary}",
            })

        for msg in kept_messages:
            formatted_messages.append({"role": msg.role, "content": msg.content})

        return formatted_messages

    # ── Agent-graph assembly ────────────────────────────────────────────────
    #
    # `compile_context` above serves the non-streaming /messages endpoints, which
    # pass ORM `Message` rows. The streaming chat path instead runs the LangGraph
    # orchestrator, whose history is a mix of LangChain `BaseMessage` objects and
    # plain dicts. `compile_agent_context` is the same attention-budget algorithm
    # over that shape, so the graph stops bounding context by turn count alone.

    @staticmethod
    def _agent_message_parts(msg: Any) -> tuple[str, str]:
        """Normalise a BaseMessage or dict into an OpenAI-style (role, content)."""
        if isinstance(msg, dict):
            return str(msg.get("role", "user")), str(msg.get("content", ""))
        msg_type = getattr(msg, "type", None)
        role = {"human": "user", "ai": "assistant"}.get(
            msg_type if isinstance(msg_type, str) else "", "user"
        )
        return role, str(getattr(msg, "content", "") or "")

    def budget_history(
        self,
        messages: list[Any],
        system_prompt: str,
        model: str = "llama-3.3-70b-versatile",
        min_recent_turns: int = 2,
    ) -> tuple[list[Any], list[Any], int]:
        """Split history into (keep, compress, tokens_kept) against the budget.

        Walks newest-to-oldest accumulating real token counts, always keeping at
        least ``min_recent_turns`` so the immediate exchange survives even when
        it alone exceeds the budget. Older turns are returned in chronological
        order for summarisation.

        Returns the kept messages unchanged — this method never rewrites or
        summarises, so callers stay in control of what the model actually sees.
        """
        max_context = self.get_max_context(model)
        usable = int(max_context * self.target_budget_ratio) - self.reserve_completion_tokens
        remaining = usable - self.estimate_tokens(system_prompt, model)

        keep: list[Any] = []
        compress: list[Any] = []
        used = 0
        for msg in reversed(messages):
            _, content = self._agent_message_parts(msg)
            cost = self.estimate_tokens(content, model)
            if used + cost <= remaining or len(keep) < min_recent_turns:
                keep.insert(0, msg)
                used += cost
            else:
                compress.insert(0, msg)
        return keep, compress, used

    async def summarize_agent_history(
        self, messages: list[Any], model: str = "llama-3.3-70b-versatile"
    ) -> str:
        """Summarise graph history into a dense episodic block.

        Overload-safe: summarisation is an optimisation, so any provider failure
        returns an empty string and the caller drops the overflow rather than
        failing the turn. Preserves the transcript's role labels.
        """
        if not messages:
            return ""
        transcript = "\n".join(
            f"{role.upper()}: {content}" for role, content in map(self._agent_message_parts, messages)
        )
        prompt = (
            "Summarize the key facts, user preferences, decisions, constraints, and "
            "open items from this conversation excerpt. Keep it compact and factual. "
            "Preserve identifiers, file names, and stated constraints verbatim.\n\n"
            f"{transcript}"
        )
        try:
            summary = await self.ai_client.completion(
                messages=[
                    {"role": "system", "content": "You are a concise conversation summarizer."},
                    {"role": "user", "content": prompt},
                ],
                model=model,
                max_tokens=500,
                temperature=0.2,
            )
            return str(summary).strip()
        except Exception as exc:
            logger.warning("agent_history_summarization_failed", error=str(exc))
            return ""


context_compiler = ContextCompiler()
