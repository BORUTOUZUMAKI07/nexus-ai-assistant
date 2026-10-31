"""
Messages API Router with Server-Sent Events (SSE) streaming.
Pure HTTP transport layer — delegates conversation and usage use cases
to ConversationService and UsageService (SRP + DIP).
"""
import json
import time
from collections.abc import AsyncGenerator
from uuid import UUID

import structlog
from backend.app.api.deps import (
    get_conversation_service,
    get_current_org_id,
    get_current_user,
    get_usage_service,
)
from backend.app.core.exceptions import ResourceNotFoundError
from backend.app.domain.conversation.schemas import (
    MessageCreate,
    MessageEdit,
    MessageFeedback,
    MessageResponse,
)
from backend.app.domain.usage.schemas import UsageLogCreate
from backend.app.domain.user.models import User
from backend.app.infrastructure.ai.litellm_client import (
    ai_client,
    get_last_resolved_model,
    group_primary_model,
    resolve_answer_model,
    resolve_provider,
)
from backend.app.infrastructure.cache.response_cache import response_cache
from backend.app.services.context_compiler import context_compiler
from backend.app.services.conversation_service import ConversationService
from backend.app.services.evaluation.guardrail_service import guardrail_service
from backend.app.services.evaluation.quality_service import quality_service
from backend.app.services.experiments import experiment_service
from backend.app.services.observability.cost_tracking import cost_tracking_service
from backend.app.services.observability.metrics import metrics_collector
from backend.app.services.observability.tracing import trace_span
from backend.app.services.prompt_compiler import prompt_compiler
from backend.app.services.usage_service import UsageService
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/conversations/{conversation_id}/messages", tags=["messages"])


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
        from backend.app.services.bandit_service import bandit_service

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
    formatted_context = await context_compiler.compile_context(
        messages=history,
        system_prompt=system_prompt,
        model=target_model,
    )

    # 4b. Response cache: repeated identical questions skip the LLM
    # round-trip. Fail-open and only active when RESPONSE_CACHE_ENABLED. Keyed
    # per prompt_variant so a bandit/canary variant flip never reuses an answer
    # generated under a different system prompt.
    cache_hit_payload = await response_cache.get(
        str(current_user.id), target_model, sanitized_content, prompt_variant=experiment_variant
    )
    if cache_hit_payload is None:
        try:
            from backend.app.services.semantic_response_cache import semantic_response_cache

            sem = await semantic_response_cache.get(
                str(current_user.id), target_model, sanitized_content, prompt_variant=experiment_variant
            )
            if sem is not None:
                cache_hit_payload = sem
        except Exception:  # pragma: no cover - fail-open
            cache_hit_payload = None

    # 5. Generate Completion
    if cache_hit_payload is not None:
        response_text = cache_hit_payload["content"]
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
            prompt_variant=experiment_variant,
        )
        try:
            from backend.app.services.semantic_response_cache import semantic_response_cache

            await semantic_response_cache.set(
                str(current_user.id),
                target_model,
                sanitized_content,
                response_text,
                tokens_input=prompt_tok,
                tokens_output=comp_tok,
                cost_usd=cost_tracking_service.calculate_cost(target_model, prompt_tok, comp_tok),
                prompt_variant=experiment_variant,
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
        from backend.app.services.webhook_service import WebhookService

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
    formatted_context = await context_compiler.compile_context(
        messages=history,
        system_prompt=system_prompt,
        model=target_model,
    )

    async def event_generator() -> AsyncGenerator[str, None]:
        start_time = time.time()
        collected_chunks = []

        try:
            yield f"data: {json.dumps({'event': 'start', 'user_message_id': str(user_msg.id), 'model': target_model})}\n\n"

            async with trace_span("chat_completion_stream", {"model": target_model, "conversation_id": str(conversation_id)}):
                stream = ai_client.stream_completion(
                    messages=formatted_context,
                    model=target_model,
                )

                async for chunk in stream:
                    collected_chunks.append(chunk)
                    yield f"data: {json.dumps({'event': 'token', 'token': chunk})}\n\n"

            full_response = "".join(collected_chunks)
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

        except GeneratorExit:
            # Client disconnected mid-stream. Persist whatever text was already
            # collected so the conversation isn't left with an unanswered user
            # turn, then let the generator finalize (upstream provider stream is
            # closed by ai_client.astream's finally block).
            partial = "".join(collected_chunks)
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
            yield f"data: {json.dumps({'event': 'error', 'error': str(exc)})}\n\n"

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
        raise HTTPException(status_code=400, detail=str(exc))
    return {
        "status": "success",
        "message_id": message_id,
        "prompt": target.prompt,
        "user_message_id": target.user_message_id,
    }
