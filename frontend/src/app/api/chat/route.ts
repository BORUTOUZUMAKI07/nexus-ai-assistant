/**
 * Chat API Route – proxies to FastAPI SSE and frames it as a Vercel AI SDK
 * data-stream (0: text deltas, 8: annotations, 3: errors).
 *
 * Reference: https://sdk.vercel.ai/docs/ai-sdk-ui/stream-protocol
 *
 * Two entry points, one translator:
 *
 *   POST  starts a run and streams it
 *   GET   re-attaches to a run that is already going (see the rejoin note below)
 *
 * Both go through `streamTranslation`. That is not tidiness: the two carry the
 * same frames, and a client cannot tell which it received. Two copies of the
 * translator would be two chances for them to differ, which is precisely how the
 * truncated-answer bug this change fixes came back.
 */
import "server-only";

import { NextRequest, NextResponse } from "next/server";
import { cookies } from "next/headers";
import { TOKEN_COOKIE } from "@/lib/auth";

// Server-only backend address (see lib/proxy.ts — never a NEXT_PUBLIC_ var).
const BACKEND_URL =
  process.env.BACKEND_URL ??
  process.env.NEXT_PUBLIC_API_URL ??
  "http://127.0.0.1:8000";

export const runtime = "nodejs"; // Must be Node.js for native fetch streaming

/**
 * Header carrying the backend's run id, on both entry points.
 *
 * Named as a constant on both sides of the wire because the two repos cannot
 * type-check each other: a rename here leaves a client that starts runs it can
 * never rejoin to, with no error anywhere — the same invisible failure as an
 * untranslated SSE frame. `backend/app/api/v1/conversations.py::RUN_ID_HEADER`
 * is the other half, and a backend test reads this file to check the two agree.
 */
const RUN_ID_HEADER = "X-Nexus-Run-Id";
const MESSAGE_ID_HEADER = "X-Nexus-Message-Id";

/**
 * Hard ceiling on one streamed answer.
 *
 * `req.signal` already handles the *client* walking away, but nothing bounds the
 * server side: a backend that accepts the request and then stalls — no `data:`
 * frames, no close — held the socket and spun the read loop until the platform
 * limit, and a stalled answer is indistinguishable from a slow one for as long
 * as it lasts. This is far more generous than the JSON routes' 30s because a
 * research-mode answer legitimately runs for minutes; it exists to convert an
 * indefinite hang into a legible error, not to interrupt healthy work.
 */
const STREAM_TIMEOUT_MS = 10 * 60 * 1000;

// Backend conversations and runs are UUIDs; "new" signals auto-creation.
// Anything else is rejected up front so malformed client payloads never reach the
// backend.
const CONVERSATION_ID_RE =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const UUID_RE = CONVERSATION_ID_RE;

/** Upstream stream endpoint for a conversation, or a fresh one when `new`. */
function backendStreamUrl(conversationId: unknown): string {
  const id = typeof conversationId === "string" && conversationId ? conversationId : "new";
  return `${BACKEND_URL}/api/v1/conversations/${id}/stream`;
}

/** The frame log of one run, replayable from the start or from a cursor. */
function backendRunStreamUrl(conversationId: string, runId: string): string {
  return `${BACKEND_URL}/api/v1/conversations/${conversationId}/runs/${runId}/stream`;
}

/**
 * One backend SSE frame → one AI SDK frame (or nothing).
 *
 * Returns `null` for events the data-stream protocol has no representation for,
 * which is the only honest option: emitting nothing is invisible, emitting
 * answer text for a verdict puts the critic's words in the assistant's mouth.
 *
 * `conversationId` is the client's pre-resolution id, used only for the HITL
 * fallback; every other frame carries its own data and is not embellished.
 */
function translateFrame(
  parsed: Record<string, unknown>,
  conversationId: unknown
): string | null {
  const eventType = parsed.type;

  if (eventType === "text_delta") {
    // Text delta: Vercel AI SDK format 0: prefix
    return `0:${JSON.stringify(parsed.content)}\n`;
  }

  if (
    eventType === "tool_call" ||
    eventType === "tool_result" ||
    eventType === "thinking" ||
    eventType === "citation"
  ) {
    // stream data annotations: 8: prefix for message annotations
    return `8:${JSON.stringify([{ type: eventType, data: parsed }])}\n`;
  }

  if (eventType === "hitl_request") {
    // Human-in-the-loop interrupt signal. Inject the thread id (the conversation
    // id) and a readable request string so the client can render an approval card
    // without backend context.
    const interrupted = Array.isArray(
      (parsed.state as { next?: unknown } | undefined)?.next
    )
      ? ((parsed.state as { next: unknown[] }).next)
      : [];
    const data = {
      ...parsed,
      type: "hitl_request",
      // backend now sends the resolved uuid in the event; fall back to the
      // client-supplied id only for legacy payloads.
      thread_id: parsed.thread_id ?? conversationId ?? "new",
      request:
        parsed.reason ??
        (interrupted.length > 0
          ? `The agent is waiting for your approval before continuing with "${interrupted[0]}".`
          : "The agent is waiting for your approval before continuing."),
    };
    return `8:${JSON.stringify([{ type: "hitl_request", data }])}\n`;
  }

  if (eventType === "critique" || eventType === "quality") {
    // The critic's verdict and the evidence-quality score, emitted by the backend
    // on a finished run (services/run_events.py).
    //
    // These were previously dropped on the floor: the backend emitted them, this
    // chain had no branch for them, and the test named
    // `test_leaves_critique_and_quality_events_untranslated` recorded the
    // omission as a decision. The decision was wrong — the frames land in the
    // database, the API contract holds, no test fails, and the user is simply
    // never told the answer was critiqued. That is the exact failure AGENTS.md §2
    // warns about for a new backend event, and it is invisible from the backend
    // side.
    //
    // Translated as annotations rather than text: the verdict is metadata about
    // the turn, not part of the answer, and emitting it as a text delta would put
    // it inside the model's own words. Field names mirror the backend contract
    // exactly (services/run_events.py::finished_run_events): critique carries
    // `critique` + `revision_count`; quality carries `evidence_score` +
    // `evidence_gate_passed`. These are not invented here — a renamed field would
    // translate cleanly into an annotation permanently missing its value.
    const data =
      eventType === "critique"
        ? {
            critique: parsed.critique ?? "",
            revision_count: parsed.revision_count ?? 0,
          }
        : {
            evidence_score: parsed.evidence_score ?? null,
            evidence_gate_passed: parsed.evidence_gate_passed ?? null,
          };
    return `8:${JSON.stringify([{ type: eventType, data }])}\n`;
  }

  if (eventType === "artifact") {
    // The turn was saved as a durable artifact. Only the id crosses the wire:
    // the canvas fetches the content itself, so a 120k-char document is never
    // duplicated into an SSE frame the client then has to hold in memory twice.
    return `8:${JSON.stringify([
      {
        type: "artifact",
        data: {
          artifact_id: parsed.artifact_id,
          title: parsed.title ?? "",
          version: parsed.version ?? 1,
          created: parsed.created !== false,
        },
      },
    ])}\n`;
  }

  if (eventType === "error") {
    return `3:${JSON.stringify(parsed.message)}\n`;
  }

  if (eventType === "done" && parsed.thread_id) {
    // Forward the resolved conversation id so the hook can surface it to the page
    // when a new conversation was auto-created.
    return `8:${JSON.stringify([
      { type: "conversation_created", data: { thread_id: parsed.thread_id } },
    ])}\n`;
  }

  return null;
}

/**
 * Wrap an upstream SSE body in the data-stream translation.
 *
 * Shared by both entry points so a replayed run and a live run cannot drift.
 *
 * There is deliberately no `signal` parameter. Cancellation belongs to the
 * `fetch` that produced `body`, and it is already wired there
 * (`AbortSignal.any` of the request and the timeout): by the time frames are
 * arriving, an abort has torn the upstream connection down and `reader.read()`
 * rejects on its own. A second signal here would look like it added a lever and
 * change nothing, which is worse than not offering one.
 */
function streamTranslation(
  body: ReadableStream<Uint8Array>,
  conversationId: unknown
): ReadableStream<Uint8Array> {
  const encoder = new TextEncoder();

  return new ReadableStream({
    async start(controller) {
      try {
        const reader = body.getReader();
        const textDecoder = new TextDecoder();
        let buffer = "";
        let doneReceived = false;

        while (true) {
          const { done, value } = await reader.read();
          if (done) break;

          buffer += textDecoder.decode(value, { stream: true });
          const lines = buffer.split("\n");
          buffer = lines.pop() ?? "";

          for (const line of lines) {
            if (!line.startsWith("data: ")) continue;
            const data = line.slice(6).trim();
            if (data === "[DONE]") {
              // terminate the outer read loop, not just this line loop — the
              // backend closes its generator right after, but a keep-alive must
              // not leave us looping forever.
              doneReceived = true;
              break;
            }

            try {
              const frame = translateFrame(
                JSON.parse(data) as Record<string, unknown>,
                conversationId
              );
              if (frame !== null) controller.enqueue(encoder.encode(frame));
            } catch {
              // Non-JSON SSE lines are skipped
            }
          }
          if (doneReceived) break;
        }
      } catch (error) {
        const msg = error instanceof Error ? error.message : "Stream error";
        controller.enqueue(encoder.encode(`3:${JSON.stringify(msg)}\n`));
      } finally {
        controller.close();
      }
    },
  });
}

/** Response headers for both entry points, plus the run id when there is one. */
function streamHeaders(
  runId: string | null,
  messageId: string | null = null
): Record<string, string> {
  return {
    "Content-Type": "text/plain; charset=utf-8",
    "X-Vercel-AI-Data-Stream": "v1",
    "Cache-Control": "no-cache, no-transform",
    Connection: "keep-alive",
    // A client that cannot read this cannot recover an interrupted answer, and it
    // fails silently — the run keeps going server-side either way.
    ...(runId ? { [RUN_ID_HEADER]: runId } : {}),
    // Forwarded only when the backend set it: a run with no persisted reply has
    // none, and the client's rejoin falls back to rendering the replay.
    ...(messageId ? { [MESSAGE_ID_HEADER]: messageId } : {}),
  };
}

export async function POST(req: NextRequest) {
  let body: Record<string, unknown>;
  try {
    body = await req.json();
  } catch {
    return NextResponse.json({ detail: "invalid_json_body" }, { status: 400 });
  }
  const {
    messages,
    conversationId,
    mode = "normal",
    planPreamble,
  } = body as {
    messages?: unknown;
    conversationId?: unknown;
    mode?: unknown;
    planPreamble?: unknown;
  };

  if (!Array.isArray(messages) || messages.length === 0) {
    return NextResponse.json({ detail: "messages_required" }, { status: 400 });
  }
  if (messages.length > 80) {
    return NextResponse.json({ detail: "too_many_messages" }, { status: 400 });
  }

  if (
    conversationId != null &&
    conversationId !== "new" &&
    conversationId !== "" &&
    typeof conversationId === "string" &&
    !CONVERSATION_ID_RE.test(conversationId)
  ) {
    return NextResponse.json(
      { detail: "invalid_conversation_id" },
      { status: 400 }
    );
  }

  const streamMode = typeof mode === "string" ? mode : "normal";

  const cookieStore = await cookies();
  const accessToken = cookieStore.get(TOKEN_COOKIE)?.value;

  // Open the upstream stream *before* committing to a response.
  //
  // This used to happen inside the ReadableStream's start(), which meant the 200
  // status was already on the wire by the time we could learn the backend had
  // rejected us. A 401 was therefore delivered as HTTP 200 carrying a "3:" error
  // frame, so the client's 401-recovery path never fired: an hour into a session,
  // sending a message produced a raw "Backend returned 401" banner instead of the
  // silent refresh every other call performs. It also reported auth failures as
  // successes to anything watching status codes.
  //
  // Both signals are combined so pressing Stop still cancels the upstream LLM
  // call (token billing) while a stalled backend cannot hold the socket open
  // indefinitely. `req.signal` is filtered rather than passed through: a real
  // NextRequest always has one, but `AbortSignal.any` throws outright on an
  // undefined member, so a caller that omits it would take the route down.
  const upstreamSignal = AbortSignal.any(
    [req.signal, AbortSignal.timeout(STREAM_TIMEOUT_MS)].filter(
      (s): s is AbortSignal => s !== undefined
    )
  );

  let backendRes: Response;
  try {
    backendRes = await fetch(backendStreamUrl(conversationId), {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        ...(accessToken ? { Authorization: `Bearer ${accessToken}` } : {}),
      },
      body: JSON.stringify({
        messages,
        mode: streamMode,
        stream: true,
        ...(typeof planPreamble === "string" && planPreamble
          ? { planPreamble }
          : {}),
      }),
      signal: upstreamSignal,
    });
  } catch (err) {
    // A client abort is not a failure to report: the user pressed Stop.
    if (err instanceof Error && err.name === "AbortError") {
      return new NextResponse(null, { status: 499 });
    }
    const timedOut = err instanceof Error && err.name === "TimeoutError";
    return NextResponse.json(
      { detail: timedOut ? "backend_timeout" : "backend_unavailable" },
      { status: 504, headers: timedOut ? { "Retry-After": "5" } : undefined }
    );
  }

  // Real status codes for pre-stream failures, so the client can tell "your
  // session expired" (worth a refresh) from "the backend is unwell" (not worth
  // retrying) instead of parsing an opaque string out of a 200 response.
  if (!backendRes.ok) {
    return NextResponse.json(
      { detail: `backend_rejected_${backendRes.status}` },
      { status: backendRes.status }
    );
  }
  if (!backendRes.body) {
    return NextResponse.json({ detail: "backend_stream_empty" }, { status: 502 });
  }

  return new NextResponse(
    streamTranslation(backendRes.body, conversationId),
    { headers: streamHeaders(backendRes.headers.get(RUN_ID_HEADER)) }
  );
}

/**
 * Re-attach to a run that is already in flight.
 *
 * The run is not the request's child, so this returns the frames produced while
 * nobody was listening — the answer survives a refresh instead of stopping at
 * whatever the browser happened to have received. `lastEventId` resumes from a
 * cursor; omitted, it replays from the start, which is the correct default
 * because a duplicated frame is harmless and a skipped one is a hole in the
 * answer.
 *
 * 404 is deliberately distinguishable in the body. "That run is gone" means the
 * client should forget the id and stop asking; it is not an error to show a user
 * about their own conversation.
 */
export async function GET(req: NextRequest) {
  const params = req.nextUrl?.searchParams;
  const runId = params?.get("runId") ?? "";
  const conversationId = params?.get("conversationId") ?? "";
  const lastEventId = params?.get("lastEventId") ?? "";

  if (!UUID_RE.test(runId) || !UUID_RE.test(conversationId)) {
    return NextResponse.json({ detail: "invalid_rejoin_target" }, { status: 400 });
  }

  const cookieStore = await cookies();
  const accessToken = cookieStore.get(TOKEN_COOKIE)?.value;

  const upstreamSignal = AbortSignal.any(
    [req.signal, AbortSignal.timeout(STREAM_TIMEOUT_MS)].filter(
      (s): s is AbortSignal => s !== undefined
    )
  );

  let backendRes: Response;
  try {
    backendRes = await fetch(backendRunStreamUrl(conversationId, runId), {
      method: "GET",
      headers: {
        // The standard SSE resume header. Sent only when the client has a real
        // cursor: sending `Last-Event-ID: 0` would be harmless, but sending a
        // stale one would silently drop the front of the answer, and an empty
        // header is easier to reason about than "0 means all".
        ...(lastEventId ? { "Last-Event-ID": lastEventId } : {}),
        ...(accessToken ? { Authorization: `Bearer ${accessToken}` } : {}),
      },
      signal: upstreamSignal,
    });
  } catch (err) {
    if (err instanceof Error && err.name === "AbortError") {
      return new NextResponse(null, { status: 499 });
    }
    const timedOut = err instanceof Error && err.name === "TimeoutError";
    return NextResponse.json(
      { detail: timedOut ? "backend_timeout" : "backend_unavailable" },
      { status: 504, headers: timedOut ? { "Retry-After": "5" } : undefined }
    );
  }

  if (backendRes.status === 404) {
    return NextResponse.json({ detail: "run_not_found" }, { status: 404 });
  }
  if (!backendRes.ok) {
    return NextResponse.json(
      { detail: `backend_rejected_${backendRes.status}` },
      { status: backendRes.status }
    );
  }
  if (!backendRes.body) {
    return NextResponse.json({ detail: "backend_stream_empty" }, { status: 502 });
  }

  return new NextResponse(streamTranslation(backendRes.body, conversationId), {
    headers: streamHeaders(
      backendRes.headers.get(RUN_ID_HEADER) ?? runId,
      backendRes.headers.get(MESSAGE_ID_HEADER)
    ),
  });
}
