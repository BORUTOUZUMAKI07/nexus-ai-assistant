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
from backend.app.core.spend import cached_tokens_seen, clear_spend_meter
from backend.app.domain.run.service import RunService
from backend.app.domain.usage.schemas import UsageLogCreate
from backend.app.infrastructure.ai.litellm_client import ai_client, resolve_provider
from backend.app.infrastructure.database.session import async_session_factory
from backend.app.services.byok import bind_byok_keys, load_user_byok_keys, reset_byok_keys
from backend.app.services.conversation_service import ConversationService
from backend.app.services.evaluation.quality_service import quality_service
from backend.app.services.observability.cost_tracking import cost_tracking_service
from backend.app.services.observability.tracing import trace_span
from backend.app.services.run_events import citation_payloads, finished_run_payloads
from backend.app.services.run_log import RunBroker, RunWriter, run_broker
from backend.app.services.usage_service import UsageService

logger = structlog.get_logger(__name__)

# Tool output is echoed to the client and stored in the log; an unbounded result
# would bloat every replay of the run.
TOOL_OUTPUT_LIMIT = 2000

# Frame types a node may emit mid-run through `get_stream_writer()`. The
# synthesizer writes `text_delta` pieces of its finished answer; the rest are
# forwarded if a node ever writes them. One set, referenced by both the
# custom-stream branch and the (dead, but kept) `on_custom_event` branch, so the
# two cannot disagree about what is deliverable.
_MID_RUN_FRAME_TYPES = frozenset(
    {
        "text_delta",
        "thinking",
        "status",
        "thought",
        "citation",
        "hitl_request",
        "elicitation_request",
        "error",
    }
)

# Nodes whose answer arrives through `get_stream_writer()` rather than through a
# state delta. The `messages` fallback below exists for nodes that do *not*
# stream (tree_of_thoughts is the known one); for these it is a second delivery
# of the same text, so it is skipped for them.
#
# Measured, and the reason the duplicate answer survived its first "fix": the
# synthesizer's `on_chain_end` carries `{"messages": [AIMessage(answer)]}`, and
# its writer payload arrives as a separate root `on_chain_stream`. The two are
# dispatched in an order that depends on scheduling, so on some runs the
# `on_chain_end` came first -- `_streamed_tokens` was still False, the fallback
# emitted the whole answer, and the writer chunk then emitted it again. Two
# identical `text_delta` frames, one persisted reply ("del onedel one",
# "turn twoturn two", "sweep one editedsweep one edited"). Reproduced in 2 of 8
# parallel runs; 0 of 26 serial runs, which is why it looked intermittent.
_WRITER_ANSWER_NODES = frozenset({"synthesizer"})

# Human label for each graph node, emitted as a `status` frame when that node
# starts.
#
# Why this exists: the synthesizer deliberately does not stream raw tokens (see
# `nodes._emit_answer_stream` -- PII redaction and the confidence hedge act on
# the finished text, so an unfiltered draft must never reach the screen), which
# means the *first* frame of an agent-mode run arrives only once the graph has
# finished. Measured on a live run: response opened, first frame at 53 s, stream
# closed at 80 s. The transcript sat on "Thinking…" for 53 s with nothing behind
# it, which reads as a hung request -- and was reported as one.
#
# A node start is observable from `astream_events` without touching a single
# node, so this is one hook instead of nine edits, and a node added later
# announces itself as long as it is named here. An unnamed node emits nothing
# rather than a guessed label: a wrong stage name is worse than no progress
# signal, because it describes work that is not happening.
_NODE_STATUS_LABELS = {
    "bootstrap": "Preparing the conversation",
    "planner": "Planning the answer",
    "tree_of_thoughts": "Weighing alternative plans",
    "orchestrator": "Choosing an approach",
    "subagent_dispatcher": "Delegating to a specialist",
    "tool_node": "Running tools",
    "critic_grader": "Checking evidence",
    "synthesizer": "Writing the answer",
    "artifact": "Saving the document",
}

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
    # Collected from the frames as they pass through, because this is the only
    # place a tool call exists: the graph does not keep tool calls in state, and
    # the message row is written after the stream has already ended. Without
    # this the row's `tool_calls` stayed at its `[]` default, so a reloaded
    # transcript showed the answer with no record of the work behind it --
    # `add_message` accepts the argument and nothing passed it.
    collected_tool_calls: list[dict[str, Any]] = []
    latency_ms: float = 0
    message_id: UUID | None = None
    start_time = time.time()

    # Contextvars are bound inside this task, not in the request. create_task
    # copies the request's context, so these labels cannot leak back to the
    # request that started the run, and the clears below cannot be skipped by a
    # client disconnecting — nothing in the request owns this state any more.
    bind_request_context(conversation_id=str(conversation_id), user_id=str(user_id), mode=mode)
    # BYOK: resolve the user's stored provider keys once for the whole run and
    # bind them for its duration. Bound here rather than in the request for the
    # same reason the spend meter is: the run outlives the response, and a key
    # bound in the request would be gone by the time the graph actually runs.
    # Fail-open -- a lookup failure must never cost the user their answer.
    _byok_token = None
    try:
        try:
            async with session_factory() as _byok_session:
                _byok_token = bind_byok_keys(
                    await load_user_byok_keys(_byok_session, user_id)
                )
        except Exception as exc:
            logger.warning(
                "byok_bind_failed", run_id=str(run_id), error_type=type(exc).__name__
            )
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
                    elif frame.get("type") == "tool_call":
                        # The AG-UI `TOOL_CALL_START` alias carries the same call a
                        # few lines later; matching on `tool_call` alone keeps one
                        # invocation to one stored entry.
                        collected_tool_calls.append(
                            {
                                "tool_name": frame.get("tool_name"),
                                "tool_input": frame.get("tool_input"),
                                "tool_call_id": frame.get("tool_call_id"),
                            }
                        )
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
                    tool_calls=collected_tool_calls,
                    citations=await _run_citations(graph, config),
                    model=await _run_answer_model(graph, config),
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

            # One checkpoint read, shared. The citations are needed twice -- on the
            # message row and as frames for the client and the run log -- and a
            # second `aget_state` would be a second trip to the checkpointer for
            # the same fact, in a module whose whole design is about not paying
            # for round-trips per token.
            post_run = await _post_run_frames(graph, config)
            citations = [f for f in post_run if f.get("type") == "citation"]
            # The model that actually answered, from the same checkpoint read.
            # The `model_info` frame is built from the graph's `answer_model`,
            # so the persisted row and the transcript's label cannot disagree.
            answer_model = next(
                (
                    str(f["model"])
                    for f in post_run
                    if f.get("type") == "model_info" and f.get("model")
                ),
                None,
            )

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
                tool_calls=collected_tool_calls,
                citations=citations,
                model=answer_model,
            )

            for frame in post_run:
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
        # A BYOK key left bound would be used for the next run served by this
        # task -- a credential leak, not just a stale value.
        if _byok_token is not None:
            reset_byok_keys(_byok_token)


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


def _is_assistant_output(message: Any) -> bool:
    """Whether a message in a node's state delta is the model's own output.

    A node's ``on_chain_end``/``on_chain_stream`` carries its *state update*, and
    ``bootstrap``'s update includes the user's own message. Streaming that as the
    reply is how a run that pauses before the synthesizer -- a HITL interrupt --
    persisted the prompt back to the user as though it were the answer (observed
    live: an approval run left an assistant message repeating the request).

    This is a denylist of the roles that are *input*, not output. A LangChain
    message that declares none of them -- and a bare test double with no ``.type``
    at all, which is what the fallback was written against -- counts as the
    model's output. An allowlist would have to name every assistant-shaped type
    (``AIMessageChunk`` is one) and would silently stop streaming the answer the
    day a new one appears.
    """
    return getattr(message, "type", None) not in ("human", "user", "system", "tool", "function")


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
    a compatible graph that still delivers the older name; the live path is the
    root `on_chain_stream` handled just after it.

    That branch looks for ``chunk["messages"]`` first, so a custom payload -- a
    dict with a ``type`` and no ``messages`` -- would no-op there by accident.
    The custom-payload check therefore runs *before* the ``messages`` fallback,
    because with ``stream_mode="custom"`` the root run's ``on_chain_stream``
    carries the payload while every node's own ``on_chain_stream`` still carries
    its state delta. Both are plain dicts at that branch; only the shape tells
    them apart.

    Our own critique/quality/artifact frames are unaffected: they are read
    post-run via ``aget_state`` and built by ``finished_run_payloads``, never
    streamed mid-run.

    To add a mid-run event: write it with ``get_stream_writer()``, keep the
    call's ``stream_mode="custom"`` (string, not list -- the list form delivers
    nothing), and add its ``type`` to ``_MID_RUN_FRAME_TYPES``.
    """
    _streamed_tokens = False
    # Nodes already announced, so a retried node is not announced twice into the
    # client's progress line. Not a frame cache: the frame is still persisted on
    # the first announcement, which is the only one a reader sees.
    _announced: set[str] = set()
    async for event in graph.astream_events(
        input=graph_input,
        config=config,
        version="v2",
        # Custom streams are opt-in and must be requested in STRING form. This
        # is what delivers the synthesizer's progressive `text_delta` frames to
        # the client; without it those writes are silently dropped. The list
        # form `["custom"]` is the trap -- it subscribes to nothing, and a typo
        # suppresses the root deltas too (tests/test_stream_custom_event_trap.py
        # measures both, one variable at a time).
        stream_mode="custom",
    ):
        kind = event.get("event")

        # A node starting is the only progress signal available before the
        # synthesizer's first (already-final) chunk. `name` is the node's own
        # registration name; nested runnables inside a node carry their own
        # names, and the label lookup is what filters them out.
        if kind == "on_chain_start":
            node_name = event.get("name")
            label = _NODE_STATUS_LABELS.get(node_name) if isinstance(node_name, str) else None
            if label and node_name not in _announced:
                _announced.add(node_name)
                yield {"type": "status", "stage": node_name, "label": label}, False

        if kind == "on_chat_model_stream":
            # The synthesizer's own token stream is the one thing this function
            # must not forward: its raw draft is not yet redacted or hedged (see
            # `nodes._emit_answer_stream`) and the sanitised text arrives through
            # the writer a moment later. Forwarding both put the answer on the
            # wire twice, which is measured: two identical `text_delta` frames
            # ("sweep one edited") in one run, and a regenerated reply persisted
            # as "sweep one editedsweep one edited". The provider only streams on
            # some runs, which is why it looked intermittent.
            streaming_node = (event.get("metadata") or {}).get("langgraph_node")
            if streaming_node == "synthesizer":
                continue
            chunk = event.get("data", {}).get("chunk")
            if chunk and hasattr(chunk, "content") and chunk.content:
                _streamed_tokens = True
                yield {"type": "text_delta", "content": chunk.content}, True

        elif kind in ("on_chain_stream", "on_chain_end"):
            data = event.get("data", {})
            chunk = data.get("chunk") or data.get("output")

            # A writer payload arrives as an `on_chain_stream` on the ROOT run
            # when custom streaming is on. It is discriminated by its own shape
            # (a node's state delta and a frame are both plain dicts here).
            if (
                kind == "on_chain_stream"
                and event.get("name") == "LangGraph"
                and isinstance(chunk, dict)
                and chunk.get("type") in _MID_RUN_FRAME_TYPES
            ):
                is_text = chunk.get("type") == "text_delta"
                if is_text:
                    _streamed_tokens = True
                yield chunk, is_text
                continue

            # The writer's owner already delivered this text, or is about to:
            # its `on_chain_end` output and its writer payload are the same
            # answer, and which one arrives first is a scheduling detail. See
            # `_WRITER_ANSWER_NODES` for the measured double delivery.
            if event.get("name") in _WRITER_ANSWER_NODES:
                continue

            # Fallback for nodes that do not stream token-by-token (the ToT node
            # is the known one). The flag flip below is not optional: without it
            # every later node's on_chain_end re-appends the same final message,
            # which is a bug this function shipped with once already.
            if not _streamed_tokens:
                if isinstance(chunk, dict):
                    msgs = chunk.get("messages")
                    if msgs:
                        last = msgs[-1]
                        if (
                            hasattr(last, "content")
                            and isinstance(last.content, str)
                            and last.content
                            and _is_assistant_output(last)
                        ):
                            _streamed_tokens = True
                            yield {"type": "text_delta", "content": last.content}, True

        elif kind == "on_tool_start":
            tool_input = event.get("data", {}).get("input", {})
            run_ref = event.get("run_id")
            tool_name = event.get("name")
            # One frame per invocation. This previously *also* emitted an AG-UI
            # `TOOL_CALL_START` alias carrying the same call; no client reads it
            # (the BFF understands `tool_call`/`tool_result`, and nothing here
            # speaks AG-UI), so it was silently dropped at the translator — the
            # exact "event nobody translates" failure AGENTS.md §2 names. The
            # BFF cannot translate it without pushing a second `tool_call`
            # annotation for the same invocation (the client has no dedup), so
            # the honest fix is to not emit it.
            yield {
                "type": "tool_call",
                "tool_name": tool_name,
                "tool_input": tool_input,
                "tool_call_id": run_ref,
            }, False

        elif kind == "on_tool_end":
            output = event.get("data", {}).get("output")
            run_ref = event.get("run_id")
            trimmed = str(output)[:TOOL_OUTPUT_LIMIT] if output else None
            yield {
                "type": "tool_result",
                "tool_call_id": run_ref,
                "result": trimmed,
            }, False

        elif kind == "on_custom_event":
            name = event.get("name", "")
            if name in _MID_RUN_FRAME_TYPES:
                yield {"type": name, **event.get("data", {})}, name == "text_delta"


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

        # Citations, for the same reason the frames above are post-run: the
        # synthesizer's chosen set is only known now. Emitted before the `done`
        # frame so a client that stops reading at `done` still has its evidence
        # -- and so the run log holds them, which is what makes a *rejoin* able
        # to restore a citation panel rather than a bare answer.
        frames.extend(citation_payloads(snapshot.values))

        # Which model produced the answer. Reported from the checkpoint rather
        # than the model group the request asked for, so a provider fallback is
        # visible in the transcript instead of hidden behind the picker's label.
        answer_model = snapshot.values.get("answer_model")
        if answer_model:
            frames.append({"type": "model_info", "model": str(answer_model)})

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


async def _run_citations(graph: Any, config: dict[str, Any]) -> list[dict[str, Any]]:
    """This run's citations, read from the checkpointer.

    Separate from ``_post_run_frames`` because the failure path needs the
    citations and must **not** get the critique/quality frames that come with it:
    a run that died mid-answer has no verdict to report, and emitting one would
    put a fabricated score on a partial reply.

    Fail-open to no citations. A run that failed before the synthesizer ran has
    none, and a checkpointer read that fails here is not worth losing the partial
    answer over -- the caller is about to write the user's text to the database,
    and that write matters more than the evidence attached to it.
    """
    try:
        snapshot = await graph.aget_state(config)
    except Exception as exc:
        logger.warning("run_citation_read_failed", error_type=type(exc).__name__)
        return []
    values = getattr(snapshot, "values", None)
    return citation_payloads(values) if values else []


async def _run_answer_model(graph: Any, config: dict[str, Any]) -> str | None:
    """The provider model that answered this run, read from the checkpointer.

    Only the failure path needs this: a successful run gets the same value from
    the ``model_info`` frame it already builds. Fail-open to ``None`` so a run
    that died before the synthesizer ran still persists (with the caller's
    fallback), rather than losing the partial answer to a metadata read.
    """
    try:
        snapshot = await graph.aget_state(config)
    except Exception as exc:
        logger.warning("run_answer_model_read_failed", error_type=type(exc).__name__)
        return None
    values = getattr(snapshot, "values", None) or {}
    model = values.get("answer_model")
    return str(model) if model else None


def _message_text(message: Any) -> str:
    """Flatten a message's ``content`` to text.

    ``content`` is a plain string for text turns and a list of parts for a
    multimodal turn (vision). ``" ".join(...)`` over the raw list raised
    ``TypeError: sequence item 0: expected str instance, list found``, which
    ``_persist_reply``'s broad ``except`` swallowed -- so *every* vision run
    persisted no assistant message, no usage row and no cost row, and the
    reloaded transcript silently dropped the answer. Text parts (and bare
    strings) are joined; non-text parts such as ``image_url`` contribute
    nothing to the token estimate.
    """
    raw = message.get("content", "") if isinstance(message, dict) else message
    if isinstance(raw, str):
        return raw
    if isinstance(raw, list):
        parts: list[str] = []
        for part in raw:
            if isinstance(part, str):
                parts.append(part)
            elif isinstance(part, dict):
                text = part.get("text")
                if isinstance(text, str) and text:
                    parts.append(text)
        return " ".join(parts)
    return "" if raw is None else str(raw)


# Webhook deliveries run outside the request and outside the run: the payload is
# already durable in `messages`, so a slow endpoint is never allowed to hold the
# assistant's reply hostage. A strong reference is kept because asyncio only
# holds a weak one, and a task that owns a delivery must not be collected
# mid-flight.
_WEBHOOK_TASKS: set[asyncio.Task[None]] = set()


def _spawn_webhook_dispatch(
    *,
    user_id: UUID,
    conversation_id: UUID,
    message_id: UUID,
    model: str,
    content: str,
) -> None:
    task = asyncio.create_task(
        _dispatch_message_completed(
            user_id=user_id,
            conversation_id=conversation_id,
            message_id=message_id,
            model=model,
            content=content,
        ),
        name=f"nexus-webhook-{message_id}",
    )
    _WEBHOOK_TASKS.add(task)
    task.add_done_callback(_WEBHOOK_TASKS.discard)


async def _dispatch_message_completed(
    *,
    user_id: UUID,
    conversation_id: UUID,
    message_id: UUID,
    model: str,
    content: str,
) -> None:
    """Deliver `message.completed` for a reply the run path just persisted.

    The non-streaming `POST /conversations/{id}/messages` endpoint has dispatched
    this event all along; the run path -- the one the app actually chats through
    -- never did, so every endpoint a user registered stayed silent and
    `webhook_deliveries` stayed empty after real turns. Measured live: one
    endpoint, zero deliveries. The payload mirrors that endpoint's, because a
    subscriber must not be able to tell which transport produced the turn.
    """
    try:
        # Imported here, not at module scope: `run_executor` is imported by the
        # app's startup path and this keeps the webhook layer (and its HTTP
        # client) out of that import graph.
        from backend.app.services.webhook_service import WebhookService

        async with async_session_factory() as session:
            delivered = await WebhookService(session).dispatch_event(
                event="message.completed",
                payload={
                    "conversation_id": str(conversation_id),
                    "message_id": str(message_id),
                    "role": "assistant",
                    "model": model,
                    "content": content[:2000],
                },
                user_id=user_id,
            )
        logger.info(
            "webhook_dispatch_completed",
            conversation_id=str(conversation_id),
            message_id=str(message_id),
            endpoints=delivered,
        )
    except Exception as exc:  # best-effort: a webhook must never fail a turn
        logger.warning(
            "webhook_dispatch_failed",
            conversation_id=str(conversation_id),
            error=str(exc),
        )


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
    citations: list[dict[str, Any]] | None = None,
    tool_calls: list[dict[str, Any]] | None = None,
    model: str | None = None,
) -> UUID | None:
    """Write the assistant message, usage row and cost row for this run.

    Runs on its own short-lived session, not the request-scoped one: the request
    may already be gone, and a session torn down mid-write would lose a reply
    that was fully generated and streamed to the user.

    ``model`` is the model that actually answered, read off the run's checkpoint
    by the caller. Every row here used to be stamped with ``settings.DEFAULT_MODEL``
    instead -- a configured value, not an observation -- which had three
    consequences: the reloaded transcript labelled every answer with a model the
    app no longer routes to, cost was priced against that model, and the admin
    panel's model-provenance and per-model slices aggregated all 164 rows under
    one legacy slug. The fallback to ``settings.DEFAULT_MODEL`` is only for a run
    that died before any model produced text.
    """
    if not emitted_text.strip():
        return None
    response_model = model or settings.DEFAULT_MODEL
    try:
        async with session_factory() as session:
            conv_svc = ConversationService(session)
            usage_svc = UsageService(session)
            prompt_text = " ".join(_message_text(m) for m in user_messages)
            prompt_tok = ai_client.count_tokens(prompt_text, response_model)
            comp_tok = ai_client.count_tokens(emitted_text, response_model)
            # How much of what the providers actually billed came from their
            # prefix cache. Clamped to the prompt count because the two are
            # measured differently -- the cache total comes from provider usage
            # across every model call in the run (including the planner and the
            # critic), while `prompt_tok` is a local estimate of this reply's
            # prompt -- and a cached count larger than the prompt it belongs to
            # is not a number anyone can act on.
            cached_tok = min(cached_tokens_seen(), prompt_tok)
            cost_usd = cost_tracking_service.calculate_cost(
                response_model, prompt_tok, comp_tok
            )
            last_user = ""
            if user_messages:
                last_user = _message_text(user_messages[-1])
            quality = quality_service.evaluate_response_quality(
                last_user, emitted_text, prompt_tok + comp_tok, latency_ms
            )

            assistant_msg = await conv_svc.add_message(
                conversation_id=conversation_id,
                role="assistant",
                content=emitted_text,
                parent_message_id=parent_message_id,
                model=response_model,
                # Stored, not derived. These two are the only durable record of
                # the evidence behind this answer, and the columns have existed
                # the whole time with nothing writing them -- so a reloaded
                # transcript rendered the answer with an empty citation panel
                # and no tool history, and read as though the answer had none.
                citations=citations or [],
                tool_calls=tool_calls or [],
            )
            await usage_svc.log_usage(
                UsageLogCreate(
                    user_id=user_id,
                    org_id=org_id,
                    conversation_id=conversation_id,
                    message_id=assistant_msg.id,
                    model=response_model,
                    provider=resolve_provider(response_model),
                    prompt_tokens=prompt_tok,
                    completion_tokens=comp_tok,
                    cached_tokens=cached_tok,
                    # The non-stream path passes `duration_ms` here; omitting it
                    # left every streamed run at 0, which is why
                    # `average_latency_ms` read 0 for them. `latency_ms` is in
                    # scope on the call above (`evaluate_response_quality`).
                    latency_ms=latency_ms,
                    cost_usd=cost_usd,
                    metadata_json={
                        "quality": quality,
                        # Recorded, not inferred: 0.0 here means "the provider
                        # reported no cache hit", which is the number that
                        # decides whether the static-first prompt is paying off.
                        "cached_tokens": cached_tok,
                        "cache_hit_rate": round(cached_tok / prompt_tok, 4) if prompt_tok else 0.0,
                    },
                )
            )
            await cost_tracking_service.record_cost_log(
                session=session,
                user_id=user_id,
                org_id=org_id,
                model=response_model,
                provider=resolve_provider(response_model),
                prompt_tokens=prompt_tok,
                completion_tokens=comp_tok,
            )
            await session.commit()
            # Fire-and-forget: an endpoint that is slow, down or retrying must
            # not delay the `done` frame the user is waiting on.
            _spawn_webhook_dispatch(
                user_id=user_id,
                conversation_id=conversation_id,
                message_id=assistant_msg.id,
                model=response_model,
                content=emitted_text,
            )
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
