/**
 * Chat API Route – proxies to FastAPI SSE and frames it as a Vercel AI SDK
 * data-stream (0: text deltas, 8: annotations, 3: errors).
 *
 * Reference: https://sdk.vercel.ai/docs/ai-sdk-ui/stream-protocol
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

// Backend conversations are UUIDs; "new" signals auto-creation. Anything else
// is rejected up front so malformed client payloads never reach the backend.
const CONVERSATION_ID_RE =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

/** Upstream stream endpoint for a conversation, or a fresh one when `new`. */
function backendStreamUrl(conversationId: unknown): string {
  const id = typeof conversationId === "string" && conversationId ? conversationId : "new";
  return `${BACKEND_URL}/api/v1/conversations/${id}/stream`;
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
  // This used to happen inside the ReadableStream's start(), which meant the
  // 200 status was already on the wire by the time we could learn the backend
  // had rejected us. A 401 was therefore delivered as HTTP 200 carrying a "3:"
  // error frame, so the client's 401-recovery path never fired: an hour into a
  // session, sending a message produced a raw "Backend returned 401" banner
  // instead of the silent refresh every other call performs. It also reported
  // auth failures as successes to anything watching status codes.
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

  // Stream data annotations alongside text (tool calls, citations, reasoning)
  const encoder = new TextEncoder();

  const readableStream = new ReadableStream({
    async start(controller) {
      try {
        // The upstream response was opened above, before the 200 was committed.
        const reader = backendRes.body!.getReader();
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
              // Terminate the outer read loop, not just this line loop — the
              // backend closes its generator right after, but a keep-alive
              // must not leave us looping forever.
              doneReceived = true;
              break;
            }

            try {
              const parsed = JSON.parse(data);
              const eventType = parsed.type;

              if (eventType === "text_delta") {
                // Text delta: Vercel AI SDK format 0: prefix
                const escaped = JSON.stringify(parsed.content);
                controller.enqueue(encoder.encode(`0:${escaped}\n`));
              } else if (eventType === "tool_call") {
                // Tool call annotation: 8: prefix for message annotations
                const annotation = JSON.stringify({
                  type: "tool_call",
                  data: parsed,
                });
                controller.enqueue(encoder.encode(`8:[${annotation}]\n`));
              } else if (eventType === "tool_result") {
                const annotation = JSON.stringify({
                  type: "tool_result",
                  data: parsed,
                });
                controller.enqueue(encoder.encode(`8:[${annotation}]\n`));
              } else if (eventType === "thinking") {
                // Reasoning / thinking blocks
                const annotation = JSON.stringify({
                  type: "reasoning",
                  data: { content: parsed.content },
                });
                controller.enqueue(encoder.encode(`8:[${annotation}]\n`));
              } else if (eventType === "citation") {
                const annotation = JSON.stringify({
                  type: "citation",
                  data: parsed,
                });
                controller.enqueue(encoder.encode(`8:[${annotation}]\n`));
              } else if (eventType === "hitl_request") {
                // Human-in-the-loop interrupt signal. Inject the thread id
                // (the conversation id) and a readable request string so the
                // client can render an approval card without backend context.
                const interrupted = Array.isArray(parsed?.state?.next)
                  ? parsed.state.next
                  : [];
                const data = {
                  ...parsed,
                  type: "hitl_request",
                  // Backend now sends the resolved UUID in the event; fall back
                  // to the client-supplied id only for legacy payloads.
                  thread_id: parsed.thread_id ?? conversationId ?? "new",
                  request:
                    parsed.reason ??
                    (interrupted.length > 0
                      ? `The agent is waiting for your approval before continuing with "${interrupted[0]}".`
                      : "The agent is waiting for your approval before continuing."),
                };
                const annotation = JSON.stringify({
                  type: "hitl_request",
                  data,
                });
                controller.enqueue(encoder.encode(`8:[${annotation}]\n`));
              } else if (eventType === "critique" || eventType === "quality") {
                // The critic's verdict and the evidence-quality score, emitted by
                // the backend on a finished run (services/run_events.py).
                //
                // These were previously dropped on the floor: the backend emitted
                // them, this chain had no branch for them, and the test named
                // `test_leaves_critique_and_quality_events_untranslated` recorded
                // the omission as a decision. The decision was wrong — the frames
                // land in the database, the API contract holds, no test fails, and
                // the user is simply never told the answer was critiqued. That is
                // the exact failure AGENTS.md §2 warns about for a new backend
                // event, and it is invisible from the backend side.
                //
                // Translated as annotations rather than text: the verdict is
                // metadata about the turn, not part of the answer, and emitting it
                // as a text delta would put it inside the model's own words.
                // Field names mirror the backend contract exactly
                // (services/run_events.py::finished_run_events): critique carries
                // `critique` + `revision_count`; quality carries
                // `evidence_score` + `evidence_gate_passed`. These are not
                // invented here — a renamed field would translate cleanly into an
                // annotation permanently missing its value.
                const annotation = JSON.stringify({
                  type: eventType,
                  data:
                    eventType === "critique"
                      ? {
                          critique: parsed.critique ?? "",
                          revision_count: parsed.revision_count ?? 0,
                        }
                      : {
                          evidence_score: parsed.evidence_score ?? null,
                          evidence_gate_passed: parsed.evidence_gate_passed ?? null,
                        },
                });
                controller.enqueue(encoder.encode(`8:[${annotation}]\n`));
              } else if (eventType === "artifact") {
                // The turn was saved as a durable artifact. Only the id crosses
                // the wire: the canvas fetches the content itself, so a 120k-char
                // document is never duplicated into an SSE frame the client then
                // has to hold in memory twice.
                const annotation = JSON.stringify({
                  type: "artifact",
                  data: {
                    artifact_id: parsed.artifact_id,
                    title: parsed.title ?? "",
                    version: parsed.version ?? 1,
                    created: parsed.created !== false,
                  },
                });
                controller.enqueue(encoder.encode(`8:[${annotation}]\n`));
              } else if (eventType === "error") {
                const errorChunk = `3:${JSON.stringify(parsed.message)}\n`;
                controller.enqueue(encoder.encode(errorChunk));
              } else if (eventType === "done" && parsed.thread_id) {
                // Forward the resolved conversation ID so the hook can surface
                // it to the page when a new conversation was auto-created.
                const annotation = JSON.stringify({
                  type: "conversation_created",
                  data: { thread_id: parsed.thread_id },
                });
                controller.enqueue(encoder.encode(`8:[${annotation}]\n`));
              }
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

  return new NextResponse(readableStream, {
    headers: {
      "Content-Type": "text/plain; charset=utf-8",
      "X-Vercel-AI-Data-Stream": "v1",
      "Cache-Control": "no-cache, no-transform",
      Connection: "keep-alive",
    },
  });
}
