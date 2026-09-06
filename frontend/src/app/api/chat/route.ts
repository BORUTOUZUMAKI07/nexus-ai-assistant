/**
 * Next.js 14 App Router – Chat API Route
 * Proxies to FastAPI SSE endpoint with Vercel AI SDK StreamData protocol.
 *
 * Protocol: data-stream (text chunks + structured data annotations)
 * Reference: https://sdk.vercel.ai/docs/ai-sdk-ui/stream-protocol
 */
import { NextRequest, NextResponse } from "next/server";
import { cookies } from "next/headers";
import { TOKEN_COOKIE } from "@/lib/auth";

const BACKEND_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

export const runtime = "nodejs"; // Must be Node.js for native fetch streaming

export async function POST(req: NextRequest) {
  const body = await req.json();
  const {
    messages,
    conversationId,
    userId = "anonymous",
    mode = "normal",
  } = body;

  const cookieStore = await cookies();
  const accessToken = cookieStore.get(TOKEN_COOKIE)?.value;

  // Stream data annotations alongside text (tool calls, citations, reasoning)
  const encoder = new TextEncoder();

  const readableStream = new ReadableStream({
    async start(controller) {
      try {
        const backendRes = await fetch(
          `${BACKEND_URL}/api/v1/conversations/${conversationId ?? "new"}/stream`,
          {
            method: "POST",
            headers: {
              "Content-Type": "application/json",
              ...(accessToken ? { Authorization: `Bearer ${accessToken}` } : {}),
              "X-User-ID": userId,
            },
            body: JSON.stringify({
              messages,
              mode,
              stream: true,
            }),
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

        while (true) {
          const { done, value } = await reader.read();
          if (done) break;

          buffer += textDecoder.decode(value, { stream: true });
          const lines = buffer.split("\n");
          buffer = lines.pop() ?? "";

          for (const line of lines) {
            if (!line.startsWith("data: ")) continue;
            const data = line.slice(6).trim();
            if (data === "[DONE]") break;

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
                // Human-in-the-loop interrupt signal
                const annotation = JSON.stringify({
                  type: "hitl_request",
                  data: parsed,
                });
                controller.enqueue(encoder.encode(`8:[${annotation}]\n`));
              } else if (eventType === "error") {
                const errorChunk = `3:${JSON.stringify(parsed.message)}\n`;
                controller.enqueue(encoder.encode(errorChunk));
              }
            } catch {
              // Non-JSON SSE lines are skipped
            }
          }
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
