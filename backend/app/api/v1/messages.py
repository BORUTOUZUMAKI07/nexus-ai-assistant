"""
Messages API Router with Server-Sent Events (SSE) streaming.
Pure HTTP transport layer — delegates conversation and usage use cases
to ConversationService and UsageService (SRP + DIP).
"""
import json
import time
from collections.abc import AsyncGenerator
from uuid import UUID

from backend.app.api.deps import (
    get_conversation_service,
    get_current_user,
    get_usage_service,
)
from backend.app.core.config import settings
from backend.app.core.exceptions import ResourceNotFoundError
from backend.app.domain.conversation.schemas import (
    MessageCreate,
    MessageFeedback,
    MessageResponse,
)
from backend.app.domain.usage.schemas import UsageLogCreate
from backend.app.domain.user.models import User
from backend.app.infrastructure.ai.litellm_client import ai_client
from backend.app.services.context_compiler import context_compiler
from backend.app.services.conversation_service import ConversationService
from backend.app.services.evaluation.guardrail_service import guardrail_service
from backend.app.services.evaluation.quality_service import quality_service
from backend.app.services.observability.cost_tracking import cost_tracking_service
from backend.app.services.observability.tracing import trace_span
from backend.app.services.prompt_compiler import prompt_compiler
from backend.app.services.usage_service import UsageService
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse

router = APIRouter(prefix="/conversations/{conversation_id}/messages", tags=["messages"])


@router.post("", response_model=MessageResponse)
async def send_message_sync(
    conversation_id: UUID,
    message_in: MessageCreate,
    current_user: User = Depends(get_current_user),
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
    target_model = message_in.model or settings.DEFAULT_MODEL

    system_prompt = await prompt_compiler.compile_system_prompt_cached(
        custom_instructions=message_in.system_prompt_override or conv.system_prompt,
    )
    formatted_context = await context_compiler.compile_context(
        messages=history,
        system_prompt=system_prompt,
        model=target_model,
    )

    # 5. Generate Completion
    start_time = time.time()
    async with trace_span("chat_completion_sync", {"model": target_model, "conversation_id": str(conversation_id)}):
        response_text = await ai_client.completion(
            messages=formatted_context,
            model=target_model,
        )
    duration_ms = (time.time() - start_time) * 1000

    prompt_tok = ai_client.count_tokens(str(formatted_context), target_model)
    comp_tok = ai_client.count_tokens(response_text, target_model)

    # 6. Save Assistant Message
    assistant_msg = await conv_svc.add_message(
        conversation_id=conversation_id,
        role="assistant",
        content=response_text,
        parent_message_id=user_msg.id,
        model=target_model,
        prompt_tokens=prompt_tok,
        completion_tokens=comp_tok,
    )

    # 7. Log Usage Telemetry (tokens, cost, quality)
    cost_usd = cost_tracking_service.calculate_cost(target_model, prompt_tok, comp_tok)
    quality = quality_service.evaluate_response_quality(
        sanitized_content, response_text, prompt_tok + comp_tok, duration_ms
    )
    await usage_svc._repo.log_usage(
        UsageLogCreate(
            user_id=current_user.id,
            conversation_id=conversation_id,
            message_id=assistant_msg.id,
            model=target_model,
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
        model=target_model,
        provider="groq",
        prompt_tokens=prompt_tok,
        completion_tokens=comp_tok,
    )

    return assistant_msg


@router.post("/stream")
async def send_message_stream(
    conversation_id: UUID,
    message_in: MessageCreate,
    current_user: User = Depends(get_current_user),
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
    target_model = message_in.model or settings.DEFAULT_MODEL

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

            prompt_tok = ai_client.count_tokens(str(formatted_context), target_model)
            comp_tok = ai_client.count_tokens(full_response, target_model)

            assistant_msg = await conv_svc.add_message(
                conversation_id=conversation_id,
                role="assistant",
                content=full_response,
                parent_message_id=user_msg.id,
                model=target_model,
                prompt_tokens=prompt_tok,
                completion_tokens=comp_tok,
            )

            cost_usd = cost_tracking_service.calculate_cost(target_model, prompt_tok, comp_tok)
            quality = quality_service.evaluate_response_quality(
                sanitized_content, full_response, prompt_tok + comp_tok, duration_ms
            )
            await usage_svc._repo.log_usage(
                UsageLogCreate(
                    user_id=current_user.id,
                    conversation_id=conversation_id,
                    message_id=assistant_msg.id,
                    model=target_model,
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
                model=target_model,
                provider="groq",
                prompt_tokens=prompt_tok,
                completion_tokens=comp_tok,
            )

            yield f"data: {json.dumps({'event': 'done', 'assistant_message_id': str(assistant_msg.id), 'tokens': comp_tok, 'latency_ms': duration_ms})}\n\n"

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
            message_id, feedback=feedback_in.feedback, note=feedback_in.feedback_note
        )
        return {"status": "success", "message_id": message_id, "feedback": feedback_in.feedback}
    except ResourceNotFoundError:
        raise HTTPException(status_code=404, detail="Message not found")
