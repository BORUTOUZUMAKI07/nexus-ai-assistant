"""
Context Compiler and Attention Budget Compactor.
Implements sliding-window conversation compaction, token budgeting,
and summary preservation to prevent context overflow.
"""
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

    def estimate_tokens(self, text: str, model: str = "llama-3.3-70b-versatile") -> int:
        return ai_client.count_tokens(text, model)

    def get_max_context(self, model: str) -> int:
        return MODEL_CONTEXT_LIMITS.get(model, MODEL_CONTEXT_LIMITS["default"])

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


context_compiler = ContextCompiler()
