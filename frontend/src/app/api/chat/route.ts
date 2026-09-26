/**
 * Chat API Route – proxies to FastAPI SSE and frames it as a Vercel AI SDK
 * data-stream (0: text deltas, 8: annotations, 3: errors).
 *
 * Reference: https://sdk.vercel.ai/docs/ai-sdk-ui/stream-protocol
 */
import { NextRequest, NextResponse } from "next/server";
import { cookies } from "next/headers";
import { TOKEN_COOKIE } from "@/lib/auth";

// Server-only backend address (see lib/proxy.ts — never a NEXT_PUBLIC_ var).
const BACKEND_URL =
  process.env.BACKEND_URL ??
  process.env.NEXT_PUBLIC_API_URL ??
  "http://127.0.0.1:8000";

export const runtime = "nodejs"; // Must be Node.js for native fetch streaming

// Backend conversations are UUIDs; "new" signals auto-creation. Anything else
// is rejected up front so malformed client payloads never reach the backend.
const CONVERSATION_ID_RE =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

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
  } = body as {
    messages?: unknown;
    conversationId?: unknown;
    mode?: unknown;
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

  // Stream data annotations alongside text (tool calls, citations, reasoning)
  const encoder = new TextEncoder();

  const readableStream = new ReadableStream({
    async start(controller) {
      try {
        const backendRes = await fetch(
          `${BACKEND_URL}/api/v1/conversations/${conversationId || "new"}/stream`,
          {
            method: "POST",
            headers: {
              "Content-Type": "application/json",
              ...(accessToken ? { Authorization: `Bearer ${accessToken}` } : {}),
            },
            body: JSON.stringify({
              messages,
              mode: streamMode,
              stream: true,
            }),
            // Forward the client abort so pressing Stop cancels the upstream
            // LLM stream instead of draining it to completion (token billing).
            signal: req.signal,
          }
        );

        if (!backendRes.ok || !backendRes.body) {
          // Return structured error in Vercel AI SDK format
          const errorChunk = `3:"Backend returned ${backendRes.status}"\n`;
          controller.enqueue(encoder.encode(errorChunk));
          controller.close();
          return;
        }

        const reader = backendRes.body.getReader();
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
