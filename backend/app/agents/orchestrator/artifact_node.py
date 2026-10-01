"""
Graph node: turn a finished turn into a durable artifact.

Placed after the synthesizer and before ``END``, so it sees the final,
post-critique, post-guardrail text -- the exact text the user is reading. An
artifact generated from an earlier draft would be a different document from the
one in the chat.

What this node deliberately does *not* do is generate content. The decision and
all the metadata live in ``services/artifact_intent.py``; the body is the
synthesizer's ``response_text``, verbatim. See that module for why a second
generation would be both expensive and wrong.

Failure is always free. Every path returns a state delta that only adds keys;
a turn that cannot produce an artifact still produces its answer. Nothing in
here may raise into the graph, because a graph exception costs the user their
entire reply to save them one file.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

import structlog
from backend.app.agents.orchestrator.state import AgentState
from backend.app.core.config import settings
from backend.app.core.spend import budget_exhausted
from backend.app.services.artifact_intent import IntentConfig, decide

logger = structlog.get_logger(__name__)

#: Refuse to store an artifact body larger than this. The schema's own cap is
#: 200k; this is deliberately tighter because a body that big means the model
#: ran away, and storing it would be storing the symptom.
MAX_ARTIFACT_CHARS = 120_000


def _last_assistant_text(state: AgentState) -> str:
    """The synthesizer's answer, from the message stream.

    Read from ``messages`` rather than a dedicated state key because
    ``messages`` is the one field the ``add_messages`` reducer keeps correct as
    the graph runs -- a separate key would have to be threaded through the
    synthesizer's return by hand and would drift the moment another node
    appended a message.
    """
    messages = state.get("messages") or []
    for message in reversed(messages):
        if getattr(message, "type", None) == "ai":
            content = message.content
            if isinstance(content, str):
                return content
            if isinstance(content, list):
                # Multimodal answers are a list of parts; keep the text ones so
                # a vision turn can still produce a document.
                return " ".join(
                    part.get("text", "")
                    for part in content
                    if isinstance(part, dict) and part.get("type") == "text"
                ).strip()
    return ""


def _last_user_text(state: AgentState) -> str:
    messages = state.get("messages") or []
    for message in reversed(messages):
        if getattr(message, "type", None) in ("human", "user"):
            content = getattr(message, "content", "")
            if isinstance(content, str):
                return content
    return ""


def _can_spend() -> bool:
    """Whether the run's budget has room for an optional classification call."""
    try:
        blocked, _reason = budget_exhausted()
    except Exception:
        # A budgeting bug must not silently remove the feature. If the meter
        # cannot be read, allow the call and let the ceiling be enforced by the
        # generation call itself.
        return True
    return not blocked


def _parse_uuid(value: Any) -> UUID | None:
    try:
        return UUID(str(value)) if value else None
    except (ValueError, TypeError, AttributeError):
        return None


async def artifact_node(state: AgentState) -> dict[str, Any]:
    """Persist the turn's answer as an artifact when it is a durable document."""
    try:
        return await _artifact_node(state)
    except Exception as exc:
        # The one place this module is allowed to be loud is also the one place
        # it must not be fatal: the answer is already written and the user is
        # already reading it.
        logger.warning("artifact_node_failed_non_blocking", error=str(exc))
        return {}


async def _artifact_node(state: AgentState) -> dict[str, Any]:
    answer = _last_assistant_text(state)
    if not answer.strip():
        return {}

    user_id = _parse_uuid(state.get("user_id"))
    conversation_id = _parse_uuid(state.get("conversation_id"))
    if user_id is None or conversation_id is None:
        # Without both ids the artifact cannot be owner-scoped, and an
        # unscoped artifact is a data leak. Refuse rather than guess.
        logger.info("artifact_node_skipped_missing_identity")
        return {}

    if len(answer) > MAX_ARTIFACT_CHARS:
        logger.info(
            "artifact_node_skipped_oversized",
            chars=len(answer),
            cap=MAX_ARTIFACT_CHARS,
        )
        return {}

    intent = await decide(
        user_text=_last_user_text(state),
        answer=answer,
        mode=str(state.get("mode") or "normal"),
        config=IntentConfig(
            # Read directly rather than through getattr-with-a-default: these
            # fields all exist in config.py, and repeating their defaults here
            # is how a tuned value and its fallback silently drift apart. A
            # rename now raises inside `_artifact_node`, which the guard in
            # `artifact_node` logs as a warning -- loud enough to notice, and
            # still not fatal to the reply.
            enabled=settings.ARTIFACT_GENERATION_ENABLED,
            min_document_chars=settings.ARTIFACT_MIN_DOCUMENT_CHARS,
            min_code_chars=settings.ARTIFACT_MIN_CODE_CHARS,
            min_code_share=settings.ARTIFACT_MIN_CODE_SHARE,
            min_classifier_chars=settings.ARTIFACT_MIN_CLASSIFIER_CHARS,
            allow_classifier=settings.ARTIFACT_ALLOW_CLASSIFIER,
        ),
        can_spend=_can_spend,
    )

    if not intent.create:
        logger.info(
            "artifact_not_created",
            reason=intent.reason,
            used_model=intent.used_model,
        )
        return {}

    artifact, created = await _persist(
        user_id=user_id,
        conversation_id=conversation_id,
        title=intent.title,
        language=intent.language,
        mime_type=intent.mime_type,
        content=answer,
    )
    if artifact is None:
        return {}

    logger.info(
        "artifact_generated",
        artifact_id=str(artifact.id),
        version=artifact.version,
        created=created,
        reason=intent.reason,
        used_model=intent.used_model,
        language=intent.language,
        chars=len(answer),
    )

    return {
        "artifact_id": str(artifact.id),
        "artifact_title": artifact.title,
        "artifact_version": artifact.version,
        "artifact_created": created,
        "artifact_reason": intent.reason,
    }


async def _persist(
    *,
    user_id: UUID,
    conversation_id: UUID,
    title: str,
    language: str,
    mime_type: str,
    content: str,
) -> tuple[Any, bool]:
    """Create the artifact, or add a version if this document already exists.

    Returns ``(artifact, created)``; ``(None, False)`` on failure.

    Opens its own short-lived session, for the same reason the memory mirror
    does: the graph holds no request-scoped DB session. That is also what makes
    the failure mode cheap -- a write that does not land costs the user a file,
    never a reply.
    """
    from backend.app.domain.artifact.repository import ArtifactRepository
    from backend.app.infrastructure.database.session import async_session_factory

    try:
        async with async_session_factory() as session:
            repo = ArtifactRepository(session)

            existing = await repo.find_by_conversation_title(
                user_id=user_id, conversation_id=conversation_id, title=title
            )
            if existing is not None:
                artifact = await repo.bump_version(
                    existing,
                    {
                        "content": content,
                        "language": language,
                        "mime_type": mime_type,
                    },
                )
                return artifact, False

            artifact = await repo.create_artifact(
                {
                    "user_id": user_id,
                    "conversation_id": conversation_id,
                    "title": title,
                    "language": language,
                    "mime_type": mime_type,
                    "content": content,
                }
            )
            return artifact, True
    except Exception as exc:
        logger.warning("artifact_persist_failed_non_blocking", error=str(exc))
        return None, False


__all__ = ["artifact_node", "MAX_ARTIFACT_CHARS"]
