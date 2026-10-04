"""
Run executor — one agent run, from graph invocation to durable completion.

Extracted from the SSE endpoint's async generator so that a run's lifetime is no
longer the HTTP request's. The endpoint is now only a *reader*: it tails this
run's frames out of the durable log (``services/run_log.py``) and formats them as
SSE. Everything that decides what a frame says lives here, so a rejoining client
is served by the same code path that produced the original stream rather than a
reconstruction of it.

Two behaviours changed, both deliberately:

* **The client-disconnect break is gone.** It used to sit inside the event loop,
  which meant a refresh cancelled the run and threw away the rest of the answer.
  A run is now bounded by the things that were always supposed to bound it — the
  per-node timeout/retry policy (``agents/orchestrator/retry_policy.py``) and the
  per-run token-and-step ceiling (``core/spend.py``) — not by whether a browser
  stayed on the page.
* **The assistant reply is persisted by the run, not by the request.** It was
  already written on a fresh session (correctly, to survive request-session
  teardown), but it lived inside the generator's tail, past a ``break`` that a
  disconnect could reach.

``graph`` is injected rather than imported. The endpoint's own tests monkeypatch
``conversations.orchestrator_graph``; if the invocation moved to a module that
imported the graph itself, that seam would silently stop intercepting and every
such test would exercise the real graph instead (AGENTS.md §9.14).
"""
from __future__ import annotations

import asyncio
import json
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Coroutine
from typing import Any
from uuid import UUID

import structlog
from backend.app.core.config import settings
from backend.app.core.logging import bind_request_context, clear_request_context
from backend.app.core.spend import clear_spend_meter
from backend.app.domain.run.service import RunService
from backend.app.domain.usage.schemas import UsageLogCreate
from backend.app.infrastructure.ai.litellm_client import ai_client
from backend.app.infrastructure.database.session import async_session_factory
from backend.app.services.conversation_service import ConversationService
from backend.app.services.evaluation.quality_service import quality_service
from backend.app.services.observability.cost_tracking import cost_tracking_service
from backend.app.services.observability.tracing import trace_span
from backend.app.services.run_events import finished_run_payloads
from backend.app.services.run_log import RunBroker, RunWriter, run_broker
from backend.app.services.usage_service import UsageService

logger = structlog.get_logger(__name__)

# Tool output is echoed to the client and stored in the log; an unbounded result
# would bloat every replay of the run.
TOOL_OUTPUT_LIMIT = 2000

# Strong references to in-flight run tasks. asyncio only holds a weak reference,
# so a task nobody keeps can be garbage collected mid-run — which, for a task
# that owns the user's answer, is not an acceptable failure mode.
_ACTIVE_RUN_TASKS: set[asyncio.Task[None]] = set()


def spawn_run_task(coro: Coroutine[Any, Any, None], *, run_id: UUID) -> asyncio.Task[None]:
    """Start ``coro`` and keep it alive until it finishes."""
    task = asyncio.create_task(coro, name=f"nexus-run-{run_id}")
    _ACTIVE_RUN_TASKS.add(task)

    def _release(finished: asyncio.Task[None]) -> None:
        _ACTIVE_RUN_TASKS.discard(finished)
        # A run that died unhandled would take the answer with it and leave the
        # row reading "running" forever. execute_run handles its own errors; this
        # is the net for a failure in that handling.
        if not finished.cancelled() and finished.exception() is not None:
            logger.error(
                "run_task_raised_unhandled",
                run_id=str(run_id),
                error_type=type(finished.exception()).__name__,
            )

    task.add_done_callback(_release)
    return task


async def execute_run(
    *,
    run_id: UUID,
    graph: Any,
    config: dict[str, Any],
    graph_input: dict[str, Any],
    conversation_id: UUID,
    user_id: UUID,
    mode: str,
    org_id: UUID | None = None,
    user_messages: list[Any] | None = None,
    parent_message_id: UUID | None = None,
    on_finish: Callable[[], Awaitable[None]] | None = None,
    broker: RunBroker | None = None,
    session_factory: Callable[[], Any] = async_session_factory,
) -> None:
    """Run one agent turn to completion, durably, whether or not anyone is watching."""
    broker = broker or run_broker
    user_messages = user_messages or []
    writer = RunWriter(
        run_id=run_id,
        broker=broker,
        session_factory=session_factory,
        flush_interval=settings.RUN_EVENT_FLUSH_INTERVAL_SECONDS,
        batch_size=settings.RUN_EVENT_BATCH_SIZE,
    )

    emitted_text = ""
    latency_ms: float = 0
    message_id: UUID | None = None
    start_time = time.time()

    # Contextvars are bound inside this task, not in the request. create_task
    # copies the request's context, so these labels cannot leak back to the
    # request that started the run, and the clears below cannot be skipped by a
    # client disconnecting — nothing in the request owns this state any more.
    bind_request_context(conversation_id=str(conversation_id), user_id=str(user_id), mode=mode)
    try:
        async with trace_span(
            "agentic_stream",
            {"thread_id": config["configurable"]["thread_id"], "mode": mode, "user_id": str(user_id)},
        ):
            try:
                async for frame, is_text in iter_graph_frames(
                    graph, config=config, graph_input=graph_input
                ):
                    await writer.emit(frame)
                    if is_text:
                        emitted_text += str(frame.get("content", ""))
            except asyncio.CancelledError:
                # Shutdown, not a client going away: the run is genuinely being
                # torn down, so it must not be marked completed.
                logger.warning("run_cancelled", run_id=str(run_id))
                await _terminate(
                    run_id,
                    session_factory,
                    writer,
                    broker,
                    status="failed",
                    error="run cancelled",
                    on_finish=on_finish,
                )
                raise
            except Exception as exc:
                logger.exception("stream_error", run_id=str(run_id), error=str(exc))
                # The partial answer is persisted *before* the error frame. The
                # pre-refactor code returned here and never wrote anything, so a
                # run that died half-way through left the user with text on screen
                # and nothing in the conversation — which is the defect this whole
                # module exists to remove, and a refresh would hide it rather than
                # reveal it. It is what the user actually saw; storing it is not
                # optional just because the turn ended badly.
                message_id = await _persist_reply(
                    conversation_id=conversation_id,
                    user_id=user_id,
                    org_id=org_id,
                    user_messages=user_messages,
                    emitted_text=emitted_text,
                    latency_ms=(time.time() - start_time) * 1000,
                    parent_message_id=parent_message_id,
                    run_id=run_id,
                    session_factory=session_factory,
                )
                await writer.emit({"type": "error", "message": str(exc)})
                await _terminate(
                    run_id,
                    session_factory,
                    writer,
                    broker,
                    status="failed",
                    message_id=message_id,
                    error=str(exc),
                    on_finish=on_finish,
                )
                return

            latency_ms = (time.time() - start_time) * 1000.0

            message_id = await _persist_reply(
                conversation_id=conversation_id,
                user_id=user_id,
                org_id=org_id,
                user_messages=user_messages,
                emitted_text=emitted_text,
                latency_ms=latency_ms,
                parent_message_id=parent_message_id,
                run_id=run_id,
                session_factory=session_factory,
            )

            for frame in await _post_run_frames(graph, config):
                await writer.emit(frame)

            await writer.emit(
                {
                    "type": "done",
                    "content": emitted_text,
                    "thread_id": config["configurable"]["thread_id"],
                    "latency_ms": latency_ms,
                }
            )
            await _terminate(
                run_id,
                session_factory,
                writer,
                broker,
                status="completed",
                message_id=message_id,
                on_finish=on_finish,
            )
    finally:
        clear_request_context()
        # A spend meter left bound keeps charging the next run served by this
        # task and, once exhausted, silently disables the ceiling for it too.
        clear_spend_meter()


async def _terminate(
    run_id: UUID,
    session_factory: Callable[[], Any],
    writer: RunWriter,
    broker: RunBroker,
    *,
    status: str,
    message_id: UUID | None = None,
    error: str | None = None,
    on_finish: Callable[[], Awaitable[None]] | None,
) -> None:
    """Flush, mark terminal, release subscribers.

    The order is load-bearing. The final batch must be committed before the
    status flips, because a reader decides the run is over from the status and
    would otherwise end its read missing the end of the answer. Subscribers are
    told last, for the same reason.
    """
    await writer.flush()
    try:
        async with session_factory() as session:
            await RunService(session).finish(
                run_id,
                status=status,
                message_id=message_id,
                error=error,
                event_count=writer.persisted,
            )
    except Exception as exc:
        # The frames are durable and the run really is over; failing to record
        # that leaves a row stuck on "running", which is worse than the row is
        # worth. Log it loudly and tell subscribers anyway.
        logger.error(
            "run_finish_record_failed", run_id=str(run_id), error_type=type(exc).__name__
        )
    if writer.dropped:
        logger.warning(
            "run_completed_with_dropped_frames",
            run_id=str(run_id),
            dropped=writer.dropped,
            emitted=writer.emitted,
        )
    broker.publish_end(run_id)
    if on_finish is not None:
        await on_finish()


async def iter_graph_frames(
    graph: Any, *, config: dict[str, Any], graph_input: dict[str, Any]
) -> AsyncIterator[tuple[dict[str, Any], bool]]:
    """Translate graph events into ``(frame, is_text_delta)`` pairs.

    ``is_text_delta`` is what lets the executor accumulate the reply without
    re-deriving it from the frames, and it is the reason the answer is exactly
    what the client saw: the same branch that emits the frame accumulates it.

    ── Custom streams: you must ask for them, in string form. ──

    Measured against langgraph 1.2.11, one variable at a time in a fresh process
    (``tests/test_stream_custom_event_trap.py`` runs the measurement; do not trust
    this comment over that test):

      astream_events(v2)                        -> 0 payloads
      astream_events(v2, stream_mode="custom")  -> payload, as an
          `on_chain_stream` on the ROOT `LangGraph` run, with data["chunk"] set
          to exactly what was written
      astream_events(v2, stream_mode=["custom"]) -> 0 payloads  (!)
      astream(stream_mode="custom")             -> payload, unwrapped
      astream_events(v2, stream_mode="bogus")   -> no raise, AND the root run's
          on_chain_stream is suppressed entirely

    Two traps in that table. The list form is what anyone writes when
    subscribing to more than one mode, and it delivers nothing. A typo is worse:
    it is accepted, and it silences the root deltas too, so the graph looks like
    it is producing less rather than more. Neither is self-announcing.

    `on_custom_event` is NOT an emitted event name. The string occurs once in the
    package, at langgraph/pregel/_retry.py:312, as an idle-timer touch handler on
    an internal scope class. A handler written against the tutorials is dead code
    that reads correct. The branch below that matches on it is retained only for
    the payloads a compatible graph may still deliver, and it is unreachable from
    this call as written — see the note below on why the call asks for nothing.

    The hazard that remains if custom streaming is ever switched on: the payload
    arrives as an `on_chain_stream`, which is the branch immediately below. It
    looks for ``chunk["messages"]``, finds none, and no-ops -- right by accident,
    not by design. So a mid-run event added without the root-name check produces
    no error and reaches no user.

    Our own critique/quality/artifact frames are unaffected: they are read
    post-run via ``aget_state`` and built by ``finished_run_payloads``, never
    streamed mid-run.

    To add a mid-run event: pass ``stream_mode="custom"`` (string, not list),
    match on ``event.get("name") == "LangGraph"``, and discriminate on the
    chunk's own shape.
    """
    _streamed_tokens = False
    async for event in graph.astream_events(
        input=graph_input,
        config=config,
        version="v2",
    ):
        kind = event.get("event")

        if kind == "on_chat_model_stream":
            chunk = event.get("data", {}).get("chunk")
            if chunk and hasattr(chunk, "content") and chunk.content:
                _streamed_tokens = True
                yield {"type": "text_delta", "content": chunk.content}, True

        elif kind in ("on_chain_stream", "on_chain_end"):
            # Fallback for nodes that do not stream token-by-token (the ToT node
            # is the known one). The flag flip below is not optional: without it
            # every later node's on_chain_end re-appends the same final message,
            # which is a bug this function shipped with once already.
            if not _streamed_tokens:
                data = event.get("data", {})
                chunk = data.get("chunk") or data.get("output")
                if isinstance(chunk, dict):
                    msgs = chunk.get("messages")
                    if msgs:
                        last = msgs[-1]
                        if (
                            hasattr(last, "content")
                            and isinstance(last.content, str)
                            and last.content
                        ):
                            _streamed_tokens = True
                            yield {"type": "text_delta", "content": last.content}, True

        elif kind == "on_tool_start":
            tool_input = event.get("data", {}).get("input", {})
            run_ref = event.get("run_id")
            tool_name = event.get("name")
            yield {
                "type": "tool_call",
                "tool_name": tool_name,
                "tool_input": tool_input,
                "tool_call_id": run_ref,
            }, False
            # AG-UI standardized alias: live tool-progress events with the
            # protocol's field names. New UIs can bind to TOOL_CALL_START;
            # existing clients keep tool_call.
            yield {
                "type": "TOOL_CALL_START",
                "messageId": f"ag-{run_ref}",
                "tool": tool_name,
                "input": tool_input,
                "timestamp": time.time(),
            }, False

        elif kind == "on_tool_end":
            output = event.get("data", {}).get("output")
            run_ref = event.get("run_id")
            tool_name = event.get("name")
            trimmed = str(output)[:TOOL_OUTPUT_LIMIT] if output else None
            yield {
                "type": "tool_result",
                "tool_call_id": run_ref,
                "result": trimmed,
            }, False
            yield {
                "type": "TOOL_CALL_COMPLETE",
                "messageId": f"ag-{run_ref}",
                "tool": tool_name,
                "output": trimmed,
                "timestamp": time.time(),
            }, False

        elif kind == "on_custom_event":
            name = event.get("name", "")
            data = event.get("data", {})
            if name in ("citation", "thinking", "hitl_request", "elicitation_request", "error", "text_delta"):
                yield {"type": name, **data}, name == "text_delta"


async def _post_run_frames(graph: Any, config: dict[str, Any]) -> list[dict[str, Any]]:
    """Frames describing a finished run: critic state, evidence, HITL pause, done.

    Read post-run from the checkpointer rather than streamed mid-run; see
    ``services/run_events.py`` for why that lives outside the router.
    """
    frames: list[dict[str, Any]] = []
    try:
        snapshot = await graph.aget_state(config)
    except Exception as exc:
        logger.warning("run_state_read_failed", error_type=type(exc).__name__)
        return frames

    if snapshot is not None and getattr(snapshot, "values", None):
        # Payloads, not SSE strings. The run log stores frames as JSON objects
        # and `encode_frame` serialises on the way out, so a pre-rendered
        # ``data: {...}`` string here would be stored as a JSON string and then
        # encoded a second time -- a frame the client's translator parses into
        # something with no ``type``, and drops. The critique, the quality score
        # and the artifact notification would all vanish with every test green.
        # See `finished_run_payloads` for the full account.
        frames.extend(finished_run_payloads(snapshot.values))

    if snapshot is not None and getattr(snapshot, "next", None):
        # LangGraph stores interrupts on snapshot.tasks[].interrupts (the
        # __interrupt__ values key does not exist). Surface the latest interrupt
        # value + thread id so the client can render the pending action and POST
        # the resume to the right conversation.
        interrupt_payload = None
        for task in snapshot.tasks or []:
            intr_list = getattr(task, "interrupts", None) or []
            if intr_list:
                interrupt_payload = getattr(intr_list[-1], "value", None)
                break
        frames.append(
            {
                "type": "hitl_request",
                "thread_id": config["configurable"]["thread_id"],
                "state": {
                    "next": list(snapshot.next),
                    "ts": str(snapshot.created_at),
                },
                "tool_name": (interrupt_payload or {}).get("tool_name"),
                "arguments": (interrupt_payload or {}).get("arguments"),
                "reason": (interrupt_payload or {}).get("reason"),
            }
        )
    return frames


async def _persist_reply(
    *,
    conversation_id: UUID,
    user_id: UUID,
    org_id: UUID | None,
    user_messages: list[Any],
    emitted_text: str,
    latency_ms: float,
    parent_message_id: UUID | None,
    run_id: UUID,
    session_factory: Callable[[], Any],
) -> UUID | None:
    """Write the assistant message, usage row and cost row for this run.

    Runs on its own short-lived session, not the request-scoped one: the request
    may already be gone, and a session torn down mid-write would lose a reply
    that was fully generated and streamed to the user.
    """
    if not emitted_text.strip():
        return None
    try:
        async with session_factory() as session:
            conv_svc = ConversationService(session)
            usage_svc = UsageService(session)
            prompt_text = " ".join(
                m.get("content", "") if isinstance(m, dict) else str(m)
                for m in user_messages
            )
            prompt_tok = ai_client.count_tokens(prompt_text, settings.DEFAULT_MODEL)
            comp_tok = ai_client.count_tokens(emitted_text, settings.DEFAULT_MODEL)
            cost_usd = cost_tracking_service.calculate_cost(
                settings.DEFAULT_MODEL, prompt_tok, comp_tok
            )
            last_user = ""
            if user_messages:
                last = user_messages[-1]
                last_user = last.get("content", "") if isinstance(last, dict) else str(last)
            quality = quality_service.evaluate_response_quality(
                last_user, emitted_text, prompt_tok + comp_tok, latency_ms
            )

            assistant_msg = await conv_svc.add_message(
                conversation_id=conversation_id,
                role="assistant",
                content=emitted_text,
                parent_message_id=parent_message_id,
                model=settings.DEFAULT_MODEL,
            )
            await usage_svc._repo.log_usage(
                UsageLogCreate(
                    user_id=user_id,
                    org_id=org_id,
                    conversation_id=conversation_id,
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
                session=session,
                user_id=user_id,
                org_id=org_id,
                model=settings.DEFAULT_MODEL,
                provider="groq",
                prompt_tokens=prompt_tok,
                completion_tokens=comp_tok,
            )
            await session.commit()
            return assistant_msg.id
    except Exception as exc:
        logger.warning(
            "agentic_assistant_message_persist_failed",
            run_id=str(run_id),
            error=str(exc),
        )
        return None


def encode_frame(frame: dict[str, Any]) -> str:
    """Render one frame as an SSE ``data:`` line.

    Lives here rather than in the router so the live stream and a replayed stream
    are byte-identical. A client cannot tell which it got, which is the point.
    """
    return f"data: {json.dumps(frame)}\n\n"
