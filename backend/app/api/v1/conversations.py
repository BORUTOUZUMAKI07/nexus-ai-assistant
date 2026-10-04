"""
Conversations API Router.
Pure HTTP transport layer — delegates all conversation use cases to ConversationService (SRP + DIP).
"""
import asyncio
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
from backend.app.domain.run.service import RunService
from backend.app.domain.user.models import User
from backend.app.infrastructure.database.session import async_session_factory
from backend.app.services.conversation_service import ConversationService
from backend.app.services.evaluation.guardrail_service import guardrail_service
from backend.app.services.org_service import OrganizationService
from backend.app.services.prompt_compiler import prompt_compiler
from backend.app.services.prompt_service import load_active_skills
from backend.app.services.run_executor import (
    encode_frame,
    execute_run,
    spawn_run_task,
)
from backend.app.services.run_log import tail_run
from backend.app.services.usage_service import UsageService
from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/conversations", tags=["conversations"])

# Header carrying the run id on both stream responses. Named here because the
# BFF has to forward it and a string literal repeated across two repos is how
# that forwarding silently stops working.
RUN_ID_HEADER = "X-Nexus-Run-Id"

# Header carrying the id of the message row a finished run persisted, set only
# when there is one.
#
# It exists to answer one question the client cannot otherwise answer: "is this
# run's answer already in my transcript?" A rejoin replays from frame 0, so a
# run that finished while the browser was closed produces an answer the user has
# never seen *rendered* -- but the row is in the database, and a client that also
# loads history would then show it twice. Comparing this id against the hydrated
# history is exact; comparing positions or lengths would be a guess that is wrong
# whenever two runs produce the same text or one of them produced nothing.
MESSAGE_ID_HEADER = "X-Nexus-Message-Id"

# ─── Per-thread run serialization ─────────────────────────────────────────────
# A single LangGraph thread must not be executed concurrently: a resume racing a
# fresh turn would double-run tools and corrupt the checkpointer. Guards track
# which threads are actively streaming so runaway concurrent runs are rejected
# (409) instead of duplicating execution.
#
# A single registry lock guards the active set. It is held only for O(1)
# bookkeeping — never during streaming — so contention is negligible, while the
# check-and-add is atomic (a release racing two acquires can never let both
# through) and there is no per-thread dict to leak or evict.
_active_stream_threads: set[str] = set()
_stream_registry_lock = asyncio.Lock()


async def _acquire_stream_slot(thread_id: str) -> bool:
    """Claims the streaming slot for ``thread_id``. Returns False if already in use."""
    async with _stream_registry_lock:
        if thread_id in _active_stream_threads:
            return False
        _active_stream_threads.add(thread_id)
        return True


async def _release_stream_slot(thread_id: str) -> None:
    async with _stream_registry_lock:
        _active_stream_threads.discard(thread_id)


@router.get("", response_model=list[ConversationResponse])
async def list_conversations(
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0, le=1000000),
    archived: bool = False,
    current_user: User = Depends(get_current_user),
    conv_svc: ConversationService = Depends(get_conversation_service),
):
    return await conv_svc.list_conversations(
        user_id=current_user.id, limit=limit, offset=offset, archived=archived
    )


@router.get("/search", response_model=list[ConversationResponse])
async def search_conversations(
    q: str,
    limit: int = 20,
    current_user: User = Depends(get_current_user),
    conv_svc: ConversationService = Depends(get_conversation_service),
):
    """
    Owner-scoped full-text-ish search: matches conversation titles and message
    bodies (ILIKE). Registered before /{conversation_id} so 'search' is never
    parsed as a conversation UUID.
    """
    if not q or not q.strip():
        return []
    return await conv_svc.search_conversations(current_user.id, q.strip(), limit=limit)


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
    model_config = {"extra": "forbid"}

    messages: list[dict] = Field(min_length=1, max_length=100)
    mode: Literal["normal", "agent", "code", "research"] = "normal"
    stream: bool = True
    # Approved-plan preamble: prepended to the agent system prompt when Plan
    # mode approval hands execution back to the streaming agent path.
    plan_preamble: str | None = Field(default=None, max_length=4000)


class HITLFeedbackRequest(BaseModel):
    action: Literal["approve", "reject", "modify"]
    data: dict = Field(default_factory=dict)


# ─── Streaming Chat Endpoint ─────────────────────────────────────────────────

@router.post("/{conversation_id}/stream")
async def stream_conversation(
    conversation_id: str,
    body: StreamChatRequest,
    current_user: User = Depends(get_current_user),
    conv_svc: ConversationService = Depends(get_conversation_service),
    usage_svc: UsageService = Depends(get_usage_service),
) -> StreamingResponse:
    """
    SSE streaming endpoint consumed by the Next.js /api/chat proxy.
    Emits newline-delimited JSON events conforming to the Nexus event schema.

    Starts a durable run and then *reads* it. The graph no longer runs inside
    this response's generator, so the reply survives a refresh; the frames this
    returns are produced by ``services/run_executor.py`` and read back out of
    ``services/run_log.py``. ``rejoin_run_stream`` below serves the same log, so
    a client that reconnects mid-answer gets the frames it missed rather than a
    truncated reply that looks finished.
    """
    thread_id = str(conversation_id)
    user_messages = body.messages

    # Multi-tenant attribution: resolve the caller's org once so the persisted
    # UsageLog/CostLog rows roll up per org (fail-open: an org-lookup hiccup
    # must never break the stream).
    try:
        org_id = await OrganizationService(usage_svc._repo.session).resolve_org_id(current_user.id)
    except Exception as exc:
        org_id = None
        logger.warning("org_resolution_failed_fail_open", thread_id=thread_id, error=str(exc))

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
        # history or persisting anything into it (IDOR guard). The fetched row is
        # kept so the conversation's own system_prompt can seed the compiled one.
        try:
            conv = await conv_svc.get_conversation(convo_uuid, user_id=current_user.id)
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
            **({"plan_preamble": body.plan_preamble} if body.plan_preamble else {}),
        }
    }

    # Compile the system prompt through the same path the non-streaming /messages
    # endpoints use. Previously this route passed only {"messages", "mode"} into
    # the graph, so `bootstrap_node` always fell back to its hardcoded default:
    # the prompt_templates/*.txt base, the CAG static-prefix cache and the
    # `skills` table were all bypassed for streaming chat — the path the UI
    # actually uses. Fail-open: on any error the graph keeps its own default.
    try:
        active_skills = await load_active_skills()
        compiled_system_prompt = await prompt_compiler.compile_system_prompt_cached(
            custom_instructions=getattr(conv, "system_prompt", None),
            active_skills=active_skills,
        )
        logger.info(
            "stream_system_prompt_compiled",
            conversation_id=thread_id,
            skills_count=len(active_skills),
            prompt_chars=len(compiled_system_prompt),
        )
    except Exception as exc:
        logger.warning("stream_system_prompt_compile_failed", error=str(exc))
        active_skills = []
        compiled_system_prompt = None

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
                # In-place rewrite of the text parts so the graph never sees the
                # original (unsanitized) content — parity with the text branch.
                if isinstance(user_messages[-1], dict) and isinstance(user_messages[-1].get("content"), list):
                    user_messages[-1]["content"] = [
                        {**p, "text": sanitized_text}
                        if isinstance(p, dict) and p.get("type") == "text"
                        else p
                        for p in user_messages[-1]["content"]
                    ]
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
    max_agentic_turns = 20
    graph_messages = (user_messages or [])[-max_agentic_turns:]

    # ── The run is the unit of work; this request is only a reader ─────────
    #
    # What changed: the graph used to be executed *inside* this response's async
    # generator, which made a run's lifetime the request's. A refresh closed the
    # generator, the event loop hit its disconnect break, and the rest of the
    # answer — along with the assistant message, the usage row and the cost row —
    # was simply never produced. The run is now a durable row executed by a task
    # that holds nothing from this request, and the generator below only reads
    # frames out of the run log.
    #
    # Consequence worth stating plainly: a client that disconnects mid-run no
    # longer stops the run. That is the entire point, and it means a run is now
    # bounded by the per-node timeout/retry policy and the per-run token/step
    # ceiling, not by whether a browser stayed on the page.
    async with async_session_factory() as session:
        run = await RunService(session).start_run(
            conversation_id=convo_uuid,
            user_id=current_user.id,
            thread_id=thread_id,
            mode=body.mode,
        )

    async def _release() -> None:
        # Released by the executor, not here: the run outlives this response, so
        # releasing on disconnect would let a second run start on the same
        # LangGraph thread while the first was still writing checkpoints.
        await _release_stream_slot(thread_id)

    spawn_run_task(
        execute_run(
            run_id=run.id,
            # Passed in rather than imported by the executor: the endpoint's own
            # tests monkeypatch *this module's* `orchestrator_graph`, and an
            # import inside the executor would step over that seam and run the
            # real graph (AGENTS.md §9.14).
            graph=orchestrator_graph,
            config=config,
            graph_input={
                "messages": graph_messages,
                "mode": body.mode,
                **({"system_prompt": compiled_system_prompt} if compiled_system_prompt else {}),
                **({"active_skills": [s.name for s in active_skills]} if active_skills else {}),
            },
            conversation_id=convo_uuid,
            user_id=current_user.id,
            mode=body.mode,
            org_id=org_id,
            user_messages=user_messages,
            parent_message_id=user_msg.id if user_msg else None,
            on_finish=_release,
            # Passed explicitly rather than left to the executor's default
            # argument: a default is bound at import time, so patching the module
            # global would not reach it and the test seam would look present but
            # do nothing.
            session_factory=async_session_factory,
        ),
        run_id=run.id,
    )

    return StreamingResponse(
        frame_stream(run.id, after_seq=0),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            # The one thing this response has to tell the client that the frames
            # themselves do not: the id it can reattach with after a refresh. A
            # header rather than a new frame type, because every new SSE frame
            # needs a branch in the BFF translator
            # (frontend/src/app/api/chat/route.ts) or it is silently dropped on
            # the floor — a header needs one forwarded line instead.
            RUN_ID_HEADER: str(run.id),
        },
    )


# ─── Rejoin: read a run that is already in flight, or finished ───────────────


@router.get("/{conversation_id}/runs/{run_id}/stream")
async def rejoin_run_stream(
    conversation_id: str,
    run_id: UUID,
    after_seq: int | None = Query(None, ge=0, description="Resume after this frame sequence"),
    last_event_id: str | None = Header(
        None, alias="Last-Event-ID", description="SSE-standard resume cursor"
    ),
    current_user: User = Depends(get_current_user),
) -> StreamingResponse:
    """Tail a run's frames, replaying whatever the client missed.

    This is the other half of the refactor. It is a *read* of the same log the
    live stream reads, so a client that refreshes mid-answer gets the frames it
    never saw and then follows the rest — rather than a truncated reply that
    looks complete.

    Ownership is checked before a single frame is read, against the caller's user
    id *and* the conversation in the path. The run id alone is not enough: a
    caller who guessed another user's run id would otherwise replay their answer.
    """
    async with async_session_factory() as session:
        run = await RunService(session).get_for_user(run_id, current_user.id)
    if run is None or str(run.conversation_id) != conversation_id:
        # 404 for both "does not exist" and "not yours": telling them apart
        # would confirm that a guessed run id is real.
        raise HTTPException(status_code=404, detail="Run not found")

    cursor = after_seq if after_seq is not None else parse_last_event_id(last_event_id)
    # Only present once the run has persisted its reply. Absent means "still
    # producing, or died before writing a row", which is exactly the case where
    # the client *should* render the replay instead of trusting its history.
    persisted = {MESSAGE_ID_HEADER: str(run.message_id)} if run.message_id else {}
    return StreamingResponse(
        frame_stream(run.id, after_seq=cursor),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            RUN_ID_HEADER: str(run.id),
            **persisted,
        },
    )


async def frame_stream(run_id: UUID, *, after_seq: int) -> AsyncGenerator[str, None]:
    """Render a run's frames as SSE: replay from ``after_seq``, then follow live.

    Shared by the live stream and the rejoin stream so a client cannot tell them
    apart, which is the requirement. The terminal ``[DONE]`` sentinel is emitted
    here rather than by the run, because it marks the end of *this reader*, not
    the end of the run: a second reader attaching to a finished run replays the
    same frames and then gets its own ``[DONE]``.
    """
    try:
        async for frame in tail_run(
            run_id, after_seq=after_seq, session_factory=async_session_factory
        ):
            yield encode_frame(frame)
    except asyncio.CancelledError:
        # The reader went away. The run did not: it is a durable row with its own
        # task, and re-raising keeps a normal disconnect from being logged as a
        # stream failure.
        raise
    except Exception as exc:
        logger.warning("run_tail_failed", run_id=str(run_id), error=str(exc))
        yield encode_frame({"type": "error", "message": "stream interrupted"})
    yield "data: [DONE]\n\n"


def parse_last_event_id(raw: str | None) -> int:
    """Read an SSE ``Last-Event-ID`` header as a frame sequence.

    Unparseable input becomes 0 — replay everything — because that is the
    recoverable direction: a client resending from the start sees frames it
    already had, whereas resuming from a wrong offset would silently skip part of
    the answer.
    """
    if not raw:
        return 0
    try:
        return max(0, int(raw.strip()))
    except (TypeError, ValueError):
        return 0


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
        # LangGraph exposes interrupts under snapshot.tasks[].interrupts, NOT
        # snapshot.values["__interrupt__"] — the old read was always empty,
        # which made every HITL resume return a false 400.
        pending_interrupts = []
        if snapshot is not None:
            for task in snapshot.tasks or []:
                pending_interrupts.extend(getattr(task, "interrupts", None) or [])
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
