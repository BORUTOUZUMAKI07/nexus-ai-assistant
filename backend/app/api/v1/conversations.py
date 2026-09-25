"""
Conversations API Router.
Pure HTTP transport layer — delegates all conversation use cases to ConversationService (SRP + DIP).
"""
import asyncio
import json
import time
from collections.abc import AsyncGenerator
from typing import Literal
from uuid import UUID

import structlog
from backend.app.agents.orchestrator.graph import orchestrator_graph
from backend.app.api.deps import (
    get_conversation_service,
    get_current_user,
    get_usage_service,
)
from backend.app.core.config import settings
from backend.app.core.exceptions import ResourceNotFoundError
from backend.app.domain.conversation.schemas import (
    BranchCreate,
    ConversationCreate,
    ConversationDetailResponse,
    ConversationResponse,
    ConversationUpdate,
)
from backend.app.domain.usage.schemas import UsageLogCreate
from backend.app.domain.user.models import User
from backend.app.infrastructure.ai.litellm_client import ai_client
from backend.app.services.conversation_service import ConversationService
from backend.app.services.evaluation.guardrail_service import guardrail_service
from backend.app.services.evaluation.quality_service import quality_service
from backend.app.services.observability.cost_tracking import cost_tracking_service
from backend.app.services.observability.tracing import trace_span
from backend.app.services.usage_service import UsageService
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/conversations", tags=["conversations"])

# ─── Per-thread run serialization ─────────────────────────────────────────────
# A single LangGraph thread must not be executed concurrently: a resume racing a
# fresh turn would double-run tools and corrupt the checkpointer. Guards track
# which threads are actively streaming so runaway concurrent runs are rejected
# (409) instead of duplicating execution.
_active_stream_threads: set[str] = set()
_stream_thread_locks: dict[str, asyncio.Lock] = {}


async def _acquire_stream_slot(thread_id: str) -> bool:
    """Claims the streaming slot for ``thread_id``. Returns False if already in use."""
    lock = _stream_thread_locks.setdefault(thread_id, asyncio.Lock())
    async with lock:
        if thread_id in _active_stream_threads:
            return False
        _active_stream_threads.add(thread_id)
        return True


async def _release_stream_slot(thread_id: str) -> None:
    lock = _stream_thread_locks.setdefault(thread_id, asyncio.Lock())
    async with lock:
        _active_stream_threads.discard(thread_id)


@router.get("", response_model=list[ConversationResponse])
async def list_conversations(
    limit: int = 50,
    offset: int = 0,
    archived: bool = False,
    current_user: User = Depends(get_current_user),
    conv_svc: ConversationService = Depends(get_conversation_service),
):
    return await conv_svc.list_conversations(
        user_id=current_user.id, limit=limit, offset=offset, archived=archived
    )


@router.post("", response_model=ConversationResponse, status_code=status.HTTP_201_CREATED)
async def create_conversation(
    conv_in: ConversationCreate,
    current_user: User = Depends(get_current_user),
    conv_svc: ConversationService = Depends(get_conversation_service),
):
    return await conv_svc.create_conversation(user_id=current_user.id, conv_in=conv_in)


@router.get("/{conversation_id}", response_model=ConversationDetailResponse)
async def get_conversation(
    conversation_id: UUID,
    current_user: User = Depends(get_current_user),
    conv_svc: ConversationService = Depends(get_conversation_service),
):
    try:
        return await conv_svc.get_conversation(conversation_id, user_id=current_user.id)
    except ResourceNotFoundError as exc:
        raise HTTPException(status_code=404, detail=exc.message)


@router.patch("/{conversation_id}", response_model=ConversationResponse)
async def update_conversation(
    conversation_id: UUID,
    conv_update: ConversationUpdate,
    current_user: User = Depends(get_current_user),
    conv_svc: ConversationService = Depends(get_conversation_service),
):
    try:
        return await conv_svc.update_conversation(
            conversation_id,
            user_id=current_user.id,
            update_data=conv_update.model_dump(exclude_unset=True),
        )
    except ResourceNotFoundError as exc:
        raise HTTPException(status_code=404, detail=exc.message)


@router.delete("/{conversation_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_conversation(
    conversation_id: UUID,
    current_user: User = Depends(get_current_user),
    conv_svc: ConversationService = Depends(get_conversation_service),
):
    try:
        await conv_svc.delete_conversation(conversation_id, user_id=current_user.id)
    except ResourceNotFoundError as exc:
        raise HTTPException(status_code=404, detail=exc.message)


@router.post("/{conversation_id}/fork", response_model=ConversationResponse)
async def fork_conversation(
    conversation_id: UUID,
    branch_in: BranchCreate,
    current_user: User = Depends(get_current_user),
    conv_svc: ConversationService = Depends(get_conversation_service),
):
    try:
        return await conv_svc.fork_conversation(
            conversation_id, user_id=current_user.id, branch_in=branch_in
        )
    except ResourceNotFoundError as exc:
        raise HTTPException(status_code=400, detail=exc.message)


# ─── Request / Response schemas ──────────────────────────────────────────────

class StreamChatRequest(BaseModel):
    messages: list[dict]
    mode: Literal["normal", "agent", "code", "research"] = "normal"
    stream: bool = True


class HITLFeedbackRequest(BaseModel):
    action: Literal["approve", "reject", "modify"]
    data: dict = Field(default_factory=dict)


# ─── Streaming Chat Endpoint ─────────────────────────────────────────────────

@router.post("/{conversation_id}/stream")
async def stream_conversation(
    conversation_id: str,
    body: StreamChatRequest,
    request: Request,
    current_user: User = Depends(get_current_user),
    conv_svc: ConversationService = Depends(get_conversation_service),
    usage_svc: UsageService = Depends(get_usage_service),
) -> StreamingResponse:
    """
    SSE streaming endpoint consumed by the Next.js /api/chat proxy.
    Emits newline-delimited JSON events conforming to the Nexus event schema.
    """
    thread_id = str(conversation_id)
    user_messages = body.messages

    # Resolve the thread: invalid/"new" ids get a real conversation created on
    # the fly so fresh chats persist their messages with real data.
    convo_uuid: UUID | None = None
    try:
        convo_uuid = UUID(thread_id)
    except (ValueError, AttributeError, TypeError):
        convo_uuid = None
    if convo_uuid is None:
        last_user = ""
        if user_messages:
            last_in = user_messages[-1]
            last_user = last_in.get("content") if isinstance(last_in, dict) else str(last_in)
        conv = await conv_svc.create_conversation(
            user_id=current_user.id,
            conv_in=ConversationCreate(
                title=(last_user or "New Chat")[:120],
                model=settings.DEFAULT_MODEL,
                system_prompt=None,
            ),
        )
        convo_uuid = conv.id
        thread_id = str(convo_uuid)
        logger.info("conversation_created_from_stream", conversation_id=thread_id)
    else:
        # Existing conversation UUID: verify ownership BEFORE reading the thread
        # history or persisting anything into it (IDOR guard).
        try:
            await conv_svc.get_conversation(convo_uuid, user_id=current_user.id)
        except ResourceNotFoundError:
            raise HTTPException(status_code=404, detail="Conversation not found")

    # Serialize concurrent runs per thread so a double-submit/resume can never
    # execute the same tools twice or interleave checkpointer writes.
    if not await _acquire_stream_slot(thread_id):
        raise HTTPException(
            status_code=409,
            detail="This conversation is already streaming. Please wait for it to finish.",
        )

    config = {
        "configurable": {
            "thread_id": thread_id,
            "conversation_id": thread_id,
            "user_id": str(current_user.id),
            "mode": body.mode,
        }
    }

    # Persist the latest user message so the agentic path is durably tracked,
    # mirroring the messages.py chat endpoints. The input is pass guardrailed
    # (PII redaction parity with the messages.py path).
    user_msg = None
    user_content = ""
    if user_messages:
        try:
            last_in = user_messages[-1]
            raw_content = last_in.get("content") if isinstance(last_in, dict) else str(last_in)
            if isinstance(raw_content, list):
                # Extract text component for guardrailing and text persistence
                text_parts = [
                    p.get("text", "")
                    for p in raw_content
                    if isinstance(p, dict) and p.get("type") == "text"
                ]
                text_to_validate = " ".join(text_parts).strip()
                sanitized_text = guardrail_service.validate_input(text_to_validate) if text_to_validate else ""
                user_msg = await conv_svc.add_message(
                    conversation_id=convo_uuid,
                    role="user",
                    content=sanitized_text or "[Image attached]",
                )
            else:
                user_content = str(raw_content)
                sanitized_content = guardrail_service.validate_input(user_content)
                user_msg = await conv_svc.add_message(
                    conversation_id=convo_uuid,
                    role="user",
                    content=sanitized_content,
                )
                if isinstance(user_messages[-1], dict):
                    user_messages[-1] = {**user_messages[-1], "content": sanitized_content}
        except Exception as exc:
            logger.warning("agentic_user_message_persist_failed", thread_id=thread_id, error=str(exc))

    # Bound the agentic context: only the most recent turns are replayed into the
    # graph every run, preventing unbounded context growth across a conversation.
    _MAX_AGENTIC_TURNS = 20
    graph_messages = (user_messages or [])[-_MAX_AGENTIC_TURNS:]

    async def event_generator() -> AsyncGenerator[str, None]:
        emitted_text = ""
        start_time = time.time()
        latency_ms = 0
        # Track whether on_chat_model_stream produced token-level chunks.
        # on_chain_stream emits the final AIMessage.content which would duplicate
        # the text if we already streamed individual tokens — use it only as a
        # fallback when the model did NOT stream token-by-token (e.g. ToT node).
        _streamed_tokens = False
        try:
            async with trace_span("agentic_stream", {"thread_id": thread_id, "mode": body.mode, "user_id": str(current_user.id)}):
                async for event in orchestrator_graph.astream_events(
                    input={"messages": graph_messages, "mode": body.mode},
                    config=config,
                    version="v2",
                ):
                    kind = event.get("event")

                    if kind == "on_chat_model_stream":
                        chunk = event.get("data", {}).get("chunk")
                        if chunk and hasattr(chunk, "content") and chunk.content:
                            payload = json.dumps({"type": "text_delta", "content": chunk.content})
                            emitted_text += chunk.content
                            _streamed_tokens = True
                            yield f"data: {payload}\n\n"

                    elif kind in ("on_chain_stream", "on_chain_end"):
                        # If no token-level chunks have been streamed, emit the final message
                        # content from the completed node (synthesizer or tree_of_thoughts).
                        if not _streamed_tokens:
                            data = event.get("data", {})
                            chunk = data.get("chunk") or data.get("output")
                            if isinstance(chunk, dict):
                                msgs = chunk.get("messages")
                                if msgs:
                                    last = msgs[-1]
                                    if hasattr(last, "content") and isinstance(last.content, str) and last.content:
                                        payload = json.dumps({"type": "text_delta", "content": last.content})
                                        emitted_text += last.content
                                        _streamed_tokens = True
                                        yield f"data: {payload}\n\n"

                    elif kind == "on_tool_start":
                        payload = json.dumps({
                            "type": "tool_call",
                            "tool_name": event.get("name"),
                            "tool_input": event.get("data", {}).get("input", {}),
                            "tool_call_id": event.get("run_id"),
                        })
                        yield f"data: {payload}\n\n"

                    elif kind == "on_tool_end":
                        output = event.get("data", {}).get("output")
                        payload = json.dumps({
                            "type": "tool_result",
                            "tool_call_id": event.get("run_id"),
                            "result": str(output)[:2000] if output else None,
                        })
                        yield f"data: {payload}\n\n"

                    elif kind == "on_custom_event":
                        name = event.get("name", "")
                        data = event.get("data", {})
                        if name in ("citation", "thinking", "hitl_request", "error", "text_delta"):
                            payload = json.dumps({"type": name, **data})
                            yield f"data: {payload}\n\n"

                    if await request.is_disconnected():
                        logger.info("client_disconnected_stream", thread_id=thread_id)
                        break

            latency_ms = (time.time() - start_time) * 1000

            # Persist the assistant reply so history survives the (stateless) graph.
            if emitted_text.strip():
                try:
                    prompt_tok = ai_client.count_tokens(
                        " ".join(
                            m.get("content", "") if isinstance(m, dict) else str(m)
                            for m in user_messages
                        ),
                        settings.DEFAULT_MODEL,
                    )
                    comp_tok = ai_client.count_tokens(emitted_text, settings.DEFAULT_MODEL)
                    cost_usd = cost_tracking_service.calculate_cost(settings.DEFAULT_MODEL, prompt_tok, comp_tok)
                    quality = quality_service.evaluate_response_quality(
                        user_messages[-1].get("content", "") if user_messages and isinstance(user_messages[-1], dict) else (str(user_messages[-1]) if user_messages else ""),
                        emitted_text,
                        prompt_tok + comp_tok,
                        latency_ms,
                    )

                    assistant_msg = await conv_svc.add_message(
                        conversation_id=convo_uuid,
                        role="assistant",
                        content=emitted_text,
                        parent_message_id=user_msg.id if user_msg else None,
                        model=settings.DEFAULT_MODEL,
                    )
                    await usage_svc._repo.log_usage(
                        UsageLogCreate(
                            user_id=current_user.id,
                            conversation_id=convo_uuid,
                            message_id=assistant_msg.id,
                            model=settings.DEFAULT_MODEL,
                            prompt_tokens=prompt_tok,
                            completion_tokens=comp_tok,
                            latency_ms=latency_ms,
                            cost_usd=cost_usd,
                            metadata_json={"quality": quality},
                        )
                    )
                    await cost_tracking_service.record_cost_log(
                        session=usage_svc._repo.session,
                        user_id=current_user.id,
                        model=settings.DEFAULT_MODEL,
                        provider="groq",
                        prompt_tokens=prompt_tok,
                        completion_tokens=comp_tok,
                    )
                except Exception as exc:
                    logger.warning("agentic_assistant_message_persist_failed", thread_id=thread_id, error=str(exc))

            # Surface critic/evidence state from the finished run, then check for
            # HITL pauses (graph parked at an interrupt → resume endpoint continues it).
            snapshot = await orchestrator_graph.aget_state(config)
            if snapshot is not None and getattr(snapshot, "values", None):
                values = snapshot.values
                critique = values.get("critique") if isinstance(values, dict) else None
                if critique:
                    yield f"data: {json.dumps({'type': 'critique', 'critique': critique, 'revision_count': values.get('revision_count', 0)})}\n\n"
                yield f"data: {json.dumps({'type': 'quality', 'evidence_score': values.get('evidence_score', 0.0), 'evidence_gate_passed': values.get('evidence_gate_passed', None)})}\n\n"
            if snapshot is not None and getattr(snapshot, "next", None):
                payload = json.dumps({
                    "type": "hitl_request",
                    "state": {"next": list(snapshot.next), "ts": str(snapshot.created_at)},
                })
                yield f"data: {payload}\n\n"

            yield f"data: {json.dumps({'type': 'done', 'content': emitted_text, 'thread_id': thread_id, 'latency_ms': latency_ms})}\n\n"

        except Exception as exc:
            logger.exception("stream_error", thread_id=thread_id, error=str(exc))
            yield f"data: {json.dumps({'type': 'error', 'message': str(exc)})}\n\n"

        finally:
            await _release_stream_slot(thread_id)
            yield "data: [DONE]\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ─── HITL Feedback Endpoint ──────────────────────────────────────────────────

@router.post("/{conversation_id}/hitl")
async def hitl_feedback(
    conversation_id: str,
    body: HITLFeedbackRequest,
    current_user: User = Depends(get_current_user),
    conv_svc: ConversationService = Depends(get_conversation_service),
) -> dict:
    """Resume a LangGraph graph via Command(resume=...) pattern after HITL approval."""
    from langgraph.types import Command

    thread_id = str(conversation_id)
    try:
        convo_uuid = UUID(thread_id)
    except (ValueError, AttributeError, TypeError):
        raise HTTPException(status_code=404, detail="Conversation not found")

    # Ownership check: never resume another user's parked HITL thread (IDOR guard).
    try:
        await conv_svc.get_conversation(convo_uuid, user_id=current_user.id)
    except ResourceNotFoundError:
        raise HTTPException(status_code=404, detail="Conversation not found")

    # Modified arguments are not yet applied by the graph resume node. Reject
    # this action explicitly rather than silently executing the original args.
    if body.action == "modify":
        raise HTTPException(
            status_code=422,
            detail="Modified tool arguments are not supported yet. Reject this request and submit a new request with the desired changes.",
        )

    # Serialize with any active stream on the same thread.
    if not await _acquire_stream_slot(thread_id):
        raise HTTPException(
            status_code=409,
            detail="This conversation is already streaming. Please wait for it to finish.",
        )

    try:
        # Verify the thread is actually parked at an interrupt and that the
        # parked approval has not expired — refuse to resume a stale request.
        snapshot = await orchestrator_graph.aget_state(
            {"configurable": {"thread_id": thread_id, "user_id": str(current_user.id)}}
        )
        pending_interrupts = (snapshot.values.get("__interrupt__") or []) if snapshot and snapshot.values else []
        if not pending_interrupts:
            raise HTTPException(status_code=400, detail="This conversation is not waiting for an approval.")
        # This endpoint resolves one specific tool-approval interrupt. Do not
        # resume arbitrary/future interrupt types or ambiguous multi-interrupt
        # snapshots with a generic approval decision.
        if len(pending_interrupts) != 1:
            raise HTTPException(
                status_code=409,
                detail="Multiple pending interruptions cannot be resolved by this endpoint.",
            )

        from backend.app.agents.orchestrator.hitl import is_approval_expired

        interrupt_payload = getattr(pending_interrupts[0], "value", None)
        if not isinstance(interrupt_payload, dict) or interrupt_payload.get("action") != "tool_approval":
            raise HTTPException(
                status_code=409,
                detail="The pending interruption is not a supported tool approval request.",
            )
        if not interrupt_payload.get("tool_name") or not isinstance(interrupt_payload.get("arguments"), dict):
            raise HTTPException(
                status_code=409,
                detail="The pending tool approval payload is invalid.",
            )
        if is_approval_expired(interrupt_payload):
            raise HTTPException(
                status_code=410,
                detail="This approval request has expired. Please start a new request.",
            )

        result = await orchestrator_graph.ainvoke(
            Command(resume={"action": body.action, "data": body.data}),
            config={"configurable": {"thread_id": thread_id, "user_id": str(current_user.id)}},
        )

        # Persist the resumed run's final assistant turn so an approved HITL
        # decision is durably recorded even though this endpoint is not SSE.
        if result and isinstance(result, dict) and body.action in ("approve", "modify"):
            msgs = result.get("messages") or []
            if msgs:
                last = msgs[-1]
                content = getattr(last, "content", "") or ""
                if isinstance(content, list):
                    content = "".join(part.get("text", "") if isinstance(part, dict) else str(part) for part in content)
                if content and str(content).strip():
                    try:
                        await conv_svc.add_message(
                            conversation_id=convo_uuid,
                            role="assistant",
                            content=str(content),
                            parent_message_id=None,
                            model=settings.DEFAULT_MODEL,
                        )
                    except Exception as exc:
                        logger.warning("agentic_hitl_assistant_message_persist_failed", thread_id=thread_id, error=str(exc))

        return {"status": "resumed", "action": body.action}
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("hitl_resume_error", thread_id=thread_id, error=str(exc))
        raise HTTPException(status_code=500, detail=str(exc))
    finally:
        await _release_stream_slot(thread_id)
