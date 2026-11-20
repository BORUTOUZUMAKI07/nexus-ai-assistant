"""
Messages API Router with Server-Sent Events (SSE) streaming.
Pure HTTP transport layer — delegates conversation and usage use cases
to ConversationService and UsageService (SRP + DIP).
"""
import hashlib
import json
import time
from collections.abc import AsyncGenerator
from typing import Any
from uuid import UUID

import structlog
from backend.app.api.deps import (
    get_conversation_service,
    get_current_org_id,
    get_current_user,
    get_usage_service,
)
from backend.app.core.exceptions import ResourceNotFoundError
from backend.app.core.spend import cached_tokens_seen
from backend.app.domain.conversation.schemas import (
    MessageCreate,
    MessageEdit,
    MessageFeedback,
    MessageResponse,
)
from backend.app.domain.usage.schemas import UsageLogCreate
from backend.app.domain.user.models import User
from backend.app.domain.user.repository import UserRepository
from backend.app.infrastructure.ai.litellm_client import (
    ai_client,
    get_last_resolved_model,
    group_primary_model,
    resolve_answer_model,
    resolve_provider,
)
from backend.app.infrastructure.cache.response_cache import response_cache
from backend.app.services.conversations.context import context_compiler
from backend.app.services.conversations.service import ConversationService
from backend.app.services.evaluation.guardrail_service import (
    StreamingOutputSanitizer,
    guardrail_service,
)
from backend.app.services.evaluation.quality_service import quality_service
from backend.app.services.experiments.service import experiment_service
from backend.app.services.memory.lifecycle import should_persist_memory
from backend.app.services.memory.long_term import long_term_memory
from backend.app.services.observability.cost_tracking import cost_tracking_service
from backend.app.services.observability.fallbacks import count_fallback
from backend.app.services.observability.metrics import metrics_collector
from backend.app.services.observability.tracing import trace_span
from backend.app.services.prompts.compiler import prompt_compiler
from backend.app.services.usage.service import UsageService
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/conversations/{conversation_id}/messages", tags=["messages"])


async def _recall_memory_block(query: str, user_id: UUID, endpoint: str) -> str:
    """Load long-term memories for a direct /messages turn.

    The orchestrator chat path already does this; these endpoints were the
    alternate door that skipped it. Strictly best-effort: a Mem0 outage, a
    missing key or a malformed response must never fail the user's answer.
    """
    try:
        block = await long_term_memory.build_memory_context_block(
            query=query, user_id=user_id, limit=8
        )
    except Exception as exc:
        logger.warning("direct_message_memory_recall_failed", endpoint=endpoint, error=str(exc))
        count_fallback("mem0_recall_direct_messages")
        return ""
    if block:
        logger.info("direct_message_memories_loaded", endpoint=endpoint, user_id=str(user_id))
    return block


def _memory_scoped_variant(variant: str, memory_block: str) -> str:
    """Scope the exact response cache to the memories used to build the answer.

    The cache is keyed on the question, not the whole prompt. Without this, a
    newly learned preference would be ignored until the cached answer expired.
    """
    if not memory_block:
        return variant
    digest = hashlib.sha256(memory_block.encode("utf-8")).hexdigest()[:12]
    return f"{variant}:mem0-{digest}"


async def _persist_memory_from_exchange(
    *,
    user_id: UUID,
    conversation_id: UUID,
    user_text: str,
    assistant_text: str,
    session: Any,
) -> None:
    """Write durable facts from a direct /messages turn, like the graph does.

    The write gate is the same one the orchestrator uses, so chatter never pays
    for an extraction. Mem0 owns extraction/dedupe; the local table owns the
    user-facing memory API, so extracted facts are mirrored into it. Everything
    here is best-effort: memory persistence must never fail an answer.
    """
    decision = should_persist_memory(user_text, assistant_text)
    if not decision.should_write:
        return
    try:
        extracted = await long_term_memory.add_from_conversation(
            messages=[
                {"role": "user", "content": user_text},
                {"role": "assistant", "content": assistant_text},
            ],
            user_id=user_id,
            metadata={"conversation_id": str(conversation_id)},
            agent_id="nexus-assistant",
        )
        if not extracted:
            logger.info(
                "direct_message_memory_extraction_empty",
                user_id=str(user_id),
                reason=decision.reason,
            )
            return
        created, _ = await UserRepository(session).mirror_memories(
            user_id=user_id,
            facts=extracted,
            source_conv_id=conversation_id,
        )
        logger.info(
            "direct_message_memory_saved",
            user_id=str(user_id),
            extracted=len(extracted),
            mirrored=len(created),
            reason=decision.reason,
        )
    except Exception as exc:
        logger.warning(
            "direct_message_memory_write_failed",
            user_id=str(user_id),
            error=str(exc),
        )
        count_fallback("mem0_write_direct_messages")


@router.post("", response_model=MessageResponse)
async def send_message_sync(
    conversation_id: UUID,
    message_in: MessageCreate,
    current_user: User = Depends(get_current_user),
    org_id: UUID | None = Depends(get_current_org_id),
    conv_svc: ConversationService = Depends(get_conversation_service),
    usage_svc: UsageService = Depends(get_usage_service),
):
    """Standard synchronous message endpoint."""
    # 1. Verify conversation ownership
    try:
        conv_detail = await conv_svc.get_conversation(conversation_id, user_id=current_user.id)
    except ResourceNotFoundError:
        raise HTTPException(status_code=404, detail="Conversation not found")

    conv = conv_detail

    # 2. Guardrail validation & PII redaction
    sanitized_content = guardrail_service.validate_input(message_in.content)

    # 3. Save User Message
    user_msg = await conv_svc.add_message(
        conversation_id=conversation_id,
        role="user",
        content=sanitized_content,
        parent_message_id=message_in.parent_message_id,
    )

    # 4. Context & Prompt Compilation
    history = await conv_svc.get_messages(conversation_id)
    # Whitelisted to a router group. `message_in.model or settings.DEFAULT_MODEL`
    # accepted any string, and the default was a legacy provider slug the router
    # no longer knows -- so an API caller sending an unknown model took the
    # request out of the fallback chain entirely.
    target_model = resolve_answer_model(message_in.model)

    # Canary/shadow release: deterministic user-bucket assignment for
    # the chat system prompt. No-op ("default") unless the experiment is
    # explicitly configured.
    experiment_variant = experiment_service.variant_for_user(str(current_user.id), "chat_system_prompt")
    if experiment_variant != "default":
        metrics_collector.increment(f"experiment:chat_system_prompt:{experiment_variant}")
        logger.info("chat_experiment_variant_assigned", prompt_variant=experiment_variant, user_id=str(current_user.id))

    # ε-greedy bandit override: no-op unless the experiment config
    # declares bandit: true. Cold start explores uniformly; then exploits the
    # empirically-best variant from recorded rewards. Fail-open.
    try:
        from backend.app.services.confidence.bandit import bandit_service

        bandit_choice = await bandit_service.select(
            usage_svc._repo.session, "chat_system_prompt", str(current_user.id)
        )
        if bandit_choice:
            experiment_variant = bandit_choice
            metrics_collector.increment(f"experiment:chat_system_prompt:{experiment_variant}")
            logger.info(
                "bandit_experiment_variant_assigned",
                prompt_variant=experiment_variant,
                user_id=str(current_user.id),
            )
    except Exception as exc:
        logger.warning("bandit_select_skipped", error=str(exc))

    system_prompt = await prompt_compiler.compile_system_prompt_cached(
        custom_instructions=message_in.system_prompt_override or conv.system_prompt,
    )
    if experiment_variant != "default":
        system_prompt = (
            f"{system_prompt}\n\n"
            f"# Experiment Context\n"
            f"You are running as the '{experiment_variant}' variant of this assistant."
        )

    # Mem0 recall: the same personalisation the orchestrator chat path gets.
    memory_block = await _recall_memory_block(
        query=sanitized_content, user_id=current_user.id, endpoint="sync"
    )
    if memory_block:
        system_prompt = f"{system_prompt}\n\n{memory_block}"
    cache_variant = _memory_scoped_variant(experiment_variant, memory_block)

    formatted_context = await context_compiler.compile_context(
        messages=history,
        system_prompt=system_prompt,
        model=target_model,
    )

    # 4b. Response cache: repeated identical questions skip the LLM
    # round-trip. Fail-open and only active when RESPONSE_CACHE_ENABLED. Keyed
    # per prompt_variant so a bandit/canary variant flip never reuses an answer
    # generated under a different system prompt, and per conversation so another
    # thread with the same question cannot replay this thread's context.
    cache_hit_payload = await response_cache.get(
        str(current_user.id),
        target_model,
        sanitized_content,
        prompt_variant=cache_variant,
        conversation_id=str(conversation_id),
    )
    if cache_hit_payload is None:
        try:
            from backend.app.services.memory.semantic_cache import semantic_response_cache

            sem = await semantic_response_cache.get(
                str(current_user.id), target_model, sanitized_content, prompt_variant=cache_variant
            )
            if sem is not None:
                cache_hit_payload = sem
        except Exception:  # pragma: no cover - fail-open
            cache_hit_payload = None

    # 5. Generate Completion
    if cache_hit_payload is not None:
        # Cached text was produced by a model too. Apply the same output
        # guardrail as a fresh completion so a cache hit cannot bypass it.
        response_text = guardrail_service.sanitize_output(cache_hit_payload["content"]).text
        prompt_tok = int(cache_hit_payload.get("tokens_input", 0))
        comp_tok = int(cache_hit_payload.get("tokens_output", 0))
        duration_ms = 0.0
        logger.info("chat_completion_cache_hit", conversation_id=str(conversation_id), model=target_model)
    else:
        start_time = time.time()
        async with trace_span("chat_completion_sync", {"model": target_model, "conversation_id": str(conversation_id)}):
            response_text = await ai_client.completion(
                messages=formatted_context,
                model=target_model,
            )
        duration_ms = (time.time() - start_time) * 1000

        # The main graph redacts PII before it streams and persists; this
        # endpoint must do the same *before* the cache write, the stored row,
        # the response body, telemetry and the outbound webhook. Redaction is
        # idempotent, so a cache hit that was sanitised above stays identical.
        response_text = guardrail_service.sanitize_output(response_text).text

        prompt_tok = ai_client.count_tokens(str(formatted_context), target_model)
        comp_tok = ai_client.count_tokens(response_text, target_model)
        await response_cache.set(
            str(current_user.id),
            target_model,
            sanitized_content,
            response_text,
            tokens_input=prompt_tok,
            tokens_output=comp_tok,
            cost_usd=cost_tracking_service.calculate_cost(target_model, prompt_tok, comp_tok),
            prompt_variant=cache_variant,
            conversation_id=str(conversation_id),
        )
        try:
            from backend.app.services.memory.semantic_cache import semantic_response_cache

            await semantic_response_cache.set(
                str(current_user.id),
                target_model,
                sanitized_content,
                response_text,
                tokens_input=prompt_tok,
                tokens_output=comp_tok,
                cost_usd=cost_tracking_service.calculate_cost(target_model, prompt_tok, comp_tok),
                prompt_variant=cache_variant,
            )
        except Exception as exc:  # noqa: BLE001 - cache write must not fail the reply
            logger.debug("response_cache_write_failed", error=str(exc))

    # The model that actually answered, not the group requested: a provider
    # fallback is a fact about the run, and recording the requested group here
    # is what made the admin provenance panel aggregate every row under one
    # label while the app was answering with something else.
    recorded_model = get_last_resolved_model() or group_primary_model(target_model)

    # 6. Save Assistant Message
    assistant_msg = await conv_svc.add_message(
        conversation_id=conversation_id,
        role="assistant",
        content=response_text,
        parent_message_id=user_msg.id,
        model=recorded_model,
        prompt_tokens=prompt_tok,
        completion_tokens=comp_tok,
        metadata_json={"prompt_variant": experiment_variant},
    )

    # 7. Log Usage Telemetry (tokens, cost, quality)
    cost_usd = cost_tracking_service.calculate_cost(recorded_model, prompt_tok, comp_tok)
    quality = quality_service.evaluate_response_quality(
        sanitized_content, response_text, prompt_tok + comp_tok, duration_ms
    )
    await usage_svc._repo.log_usage(
        UsageLogCreate(
            user_id=current_user.id,
            org_id=org_id,
            conversation_id=conversation_id,
            message_id=assistant_msg.id,
            model=recorded_model,
            prompt_tokens=prompt_tok,
            completion_tokens=comp_tok,
            # Prefix-cache hits as the provider reported them, clamped to the
            # prompt they belong to. `cache_hit` below is the *app's* response
            # cache answering from Redis; this is the provider's prompt cache,
            # and conflating the two would hide which one is saving tokens.
            cached_tokens=min(cached_tokens_seen(), prompt_tok),
            latency_ms=duration_ms,
            cost_usd=cost_usd,
            metadata_json={
                "quality": quality,
                "cache_hit": cache_hit_payload is not None,
                "prompt_variant": experiment_variant,
            },
        )
    )
    await cost_tracking_service.record_cost_log(
        session=usage_svc._repo.session,
        user_id=current_user.id,
        org_id=org_id,
        model=recorded_model,
        provider=resolve_provider(recorded_model),
        prompt_tokens=prompt_tok,
        completion_tokens=comp_tok,
    )

    # 8. Outbound webhook dispatch (best-effort, non-blocking): any active
    #    message.completed endpoints the user registered receive a signed event.
    try:
        from backend.app.services.webhooks.service import WebhookService

        wh_svc = WebhookService(usage_svc._repo.session)
        await wh_svc.dispatch_event(
            event="message.completed",
            payload={
                "conversation_id": str(conversation_id),
                "message_id": str(assistant_msg.id),
                "role": "assistant",
                "model": target_model,
                "content": response_text[:2000],
            },
            user_id=current_user.id,
        )
    except Exception as exc:
        logger.warning("webhook_dispatch_failed", conversation_id=str(conversation_id), error=str(exc))

    # Best-effort memory write after the answer is already complete.
    await _persist_memory_from_exchange(
        user_id=current_user.id,
        conversation_id=conversation_id,
        user_text=sanitized_content,
        assistant_text=response_text,
        session=usage_svc._repo.session,
    )

    return assistant_msg


@router.post("/stream")
async def send_message_stream(
    conversation_id: UUID,
    message_in: MessageCreate,
    current_user: User = Depends(get_current_user),
    org_id: UUID | None = Depends(get_current_org_id),
    conv_svc: ConversationService = Depends(get_conversation_service),
    usage_svc: UsageService = Depends(get_usage_service),
):
    """Server-Sent Events (SSE) token-by-token streaming endpoint."""
    try:
        conv_detail = await conv_svc.get_conversation(conversation_id, user_id=current_user.id)
    except ResourceNotFoundError:
        raise HTTPException(status_code=404, detail="Conversation not found")

    conv = conv_detail
    sanitized_content = guardrail_service.validate_input(message_in.content)

    user_msg = await conv_svc.add_message(
        conversation_id=conversation_id,
        role="user",
        content=sanitized_content,
        parent_message_id=message_in.parent_message_id,
    )

    history = await conv_svc.get_messages(conversation_id)
    target_model = resolve_answer_model(message_in.model)

    system_prompt = await prompt_compiler.compile_system_prompt_cached(
        custom_instructions=message_in.system_prompt_override or conv.system_prompt,
    )
    memory_block = await _recall_memory_block(
        query=sanitized_content, user_id=current_user.id, endpoint="stream"
    )
    if memory_block:
        system_prompt = f"{system_prompt}\n\n{memory_block}"

    formatted_context = await context_compiler.compile_context(
        messages=history,
        system_prompt=system_prompt,
        model=target_model,
    )

    async def event_generator() -> AsyncGenerator[str, None]:
        start_time = time.time()
        # PII can span provider tokens ("alice@" + "example.com"). The
        # incremental sanitiser holds a bounded tail and only emits frames it
        # has already redacted, so no frame -- and especially no persisted row
        # -- ever carries raw model text.
        sanitizer = StreamingOutputSanitizer()

        try:
            yield f"data: {json.dumps({'event': 'start', 'user_message_id': str(user_msg.id), 'model': target_model})}\n\n"

            async with trace_span("chat_completion_stream", {"model": target_model, "conversation_id": str(conversation_id)}):
                stream = ai_client.stream_completion(
                    messages=formatted_context,
                    model=target_model,
                )

                async for chunk in stream:
                    safe = sanitizer.feed(chunk)
                    if safe:
                        yield f"data: {json.dumps({'event': 'token', 'token': safe})}\n\n"

            # Flush the held tail before any persistence: the stored answer and
            # the frames already sent must concatenate to exactly this text.
            tail = sanitizer.finish()
            if tail:
                yield f"data: {json.dumps({'event': 'token', 'token': tail})}\n\n"
            full_response = sanitizer.text
            duration_ms = (time.time() - start_time) * 1000
            recorded_model = get_last_resolved_model() or group_primary_model(target_model)

            prompt_tok = ai_client.count_tokens(str(formatted_context), recorded_model)
            comp_tok = ai_client.count_tokens(full_response, recorded_model)

            assistant_msg = await conv_svc.add_message(
                conversation_id=conversation_id,
                role="assistant",
                content=full_response,
                parent_message_id=user_msg.id,
                model=recorded_model,
                prompt_tokens=prompt_tok,
                completion_tokens=comp_tok,
            )

            cost_usd = cost_tracking_service.calculate_cost(recorded_model, prompt_tok, comp_tok)
            quality = quality_service.evaluate_response_quality(
                sanitized_content, full_response, prompt_tok + comp_tok, duration_ms
            )
            await usage_svc._repo.log_usage(
                UsageLogCreate(
                    user_id=current_user.id,
                    org_id=org_id,
                    conversation_id=conversation_id,
                    message_id=assistant_msg.id,
                    model=recorded_model,
                    prompt_tokens=prompt_tok,
                    completion_tokens=comp_tok,
                    cached_tokens=min(cached_tokens_seen(), prompt_tok),
                    latency_ms=duration_ms,
                    cost_usd=cost_usd,
                    metadata_json={"quality": quality},
                )
            )
            await cost_tracking_service.record_cost_log(
                session=usage_svc._repo.session,
                user_id=current_user.id,
                org_id=org_id,
                model=recorded_model,
                provider=resolve_provider(recorded_model),
                prompt_tokens=prompt_tok,
                completion_tokens=comp_tok,
            )

            yield f"data: {json.dumps({'event': 'done', 'assistant_message_id': str(assistant_msg.id), 'tokens': comp_tok, 'latency_ms': duration_ms})}\n\n"

            # The answer is already on the wire; persist facts only afterwards.
            await _persist_memory_from_exchange(
                user_id=current_user.id,
                conversation_id=conversation_id,
                user_text=sanitized_content,
                assistant_text=full_response,
                session=usage_svc._repo.session,
            )

        except GeneratorExit:
            # Client disconnected mid-stream. Persist whatever text was already
            # collected so the conversation isn't left with an unanswered user
            # turn, then let the generator finalize (upstream provider stream is
            # closed by ai_client.astream's finally block).
            # Already-redacted text from the sanitiser. Never persist the raw
            # provider chunks: a disconnect is not a guardrail bypass.
            partial = sanitizer.text
            if partial and partial.strip():
                try:
                    await conv_svc.add_message(
                        conversation_id=conversation_id,
                        role="assistant",
                        content=partial,
                        parent_message_id=user_msg.id,
                        model=(
                            get_last_resolved_model()
                            or group_primary_model(target_model)
                        ),
                    )
                    logger.info("partial_response_persisted_on_disconnect", conversation_id=str(conversation_id))
                except Exception as exc:
                    logger.warning("partial_response_persist_failed", conversation_id=str(conversation_id), error=str(exc))
            raise

        except Exception as exc:
            # Provider and DB exceptions routinely contain API keys, DSNs and
            # request bodies. Keep the detail server-side; give the client a
            # stable, non-sensitive message on the same SSE contract.
            logger.error(
                "stream_completion_failed",
                error=str(exc),
                error_type=type(exc).__name__,
                conversation_id=str(conversation_id),
            )
            yield f"data: {json.dumps({'event': 'error', 'error': 'An error occurred while generating the response.'})}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/{message_id}/feedback")
async def record_feedback(
    conversation_id: UUID,
    message_id: UUID,
    feedback_in: MessageFeedback,
    current_user: User = Depends(get_current_user),
    conv_svc: ConversationService = Depends(get_conversation_service),
):
    try:
        await conv_svc.record_feedback(
            message_id,
            feedback=feedback_in.feedback,
            note=feedback_in.feedback_note,
            conversation_id=conversation_id,
            user_id=current_user.id,
        )
        return {"status": "success", "message_id": message_id, "feedback": feedback_in.feedback}
    except ResourceNotFoundError:
        raise HTTPException(status_code=404, detail="Message not found")


@router.delete("/{message_id}")
async def delete_message(
    conversation_id: UUID,
    message_id: UUID,
    current_user: User = Depends(get_current_user),
    conv_svc: ConversationService = Depends(get_conversation_service),
):
    """Delete a turn and everything after it.

    Deleting a user message must also take the assistant reply it produced — an
    answer left above a question that is gone reads as data loss, not as an edit.
    The service enforces that ordering cascade (see ``delete_message`` on the
    repository) rather than the client, because only the server knows how many
    rows followed.
    """
    try:
        deleted = await conv_svc.delete_message(
            message_id, conversation_id, user_id=current_user.id
        )
    except ResourceNotFoundError:
        raise HTTPException(status_code=404, detail="Message not found")
    return {"status": "success", "message_id": message_id, "deleted": deleted}


@router.patch("/{message_id}", response_model=MessageResponse)
async def edit_message(
    conversation_id: UUID,
    message_id: UUID,
    edit_in: MessageEdit,
    current_user: User = Depends(get_current_user),
    conv_svc: ConversationService = Depends(get_conversation_service),
):
    """Rewrite a user turn, invalidating the turns that followed it.

    Guardrailed on input exactly like ``POST /messages`` — an edit is a second
    door into the same column, and a path that skipped PII redaction would let
    the un-sanitised text reach the model on the resend the client performs next.
    """
    sanitized = guardrail_service.validate_input(edit_in.content)
    try:
        msg = await conv_svc.edit_message(
            message_id, sanitized, conversation_id, user_id=current_user.id
        )
    except ResourceNotFoundError:
        raise HTTPException(status_code=404, detail="Message not found")
    return msg


@router.post("/{message_id}/regenerate")
async def regenerate_message(
    conversation_id: UUID,
    message_id: UUID,
    current_user: User = Depends(get_current_user),
    conv_svc: ConversationService = Depends(get_conversation_service),
):
    """Drop an assistant reply and hand back the prompt that produced it.

    The client then re-runs the turn through the normal chat stream. Doing the
    delete server-side first is what makes the transcript consistent even if the
    resend never happens: the stale answer is gone, and the prompt it answered is
    the one the user sees.

    ``user_message_id`` is part of the response on purpose. The resend is the
    *same* turn, so the client must tell the stream route which row it already
    has; otherwise the route persists the question a second time and the
    transcript shows it twice.
    """
    try:
        target = await conv_svc.prepare_regeneration(
            message_id, conversation_id, user_id=current_user.id
        )
    except ResourceNotFoundError:
        raise HTTPException(status_code=404, detail="Message not found")
    except ValueError as exc:
        # Only the two user-facing service errors are echoed. An unexpected
        # ValueError may carry connection strings or provider details, so it is
        # logged here and replaced with a generic 400 on the wire.
        detail = str(exc)
        allowed = {
            "Only an assistant message can be regenerated",
            "No preceding user message to regenerate from",
        }
        if detail not in allowed:
            logger.warning("regenerate_error_redacted", error=detail)
            detail = "Cannot regenerate this message"
        raise HTTPException(status_code=400, detail=detail)
    return {
        "status": "success",
        "message_id": message_id,
        "prompt": target.prompt,
        "user_message_id": target.user_message_id,
    }
