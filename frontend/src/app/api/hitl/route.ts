/**
 * HITL Feedback Route
 * Sends human-in-the-loop approval/rejection back to LangGraph via FastAPI.
 */
import { NextRequest, NextResponse } from "next/server";
import { cookies } from "next/headers";
import { TOKEN_COOKIE } from "@/lib/auth";

// Server-only backend address (see lib/proxy.ts — never a NEXT_PUBLIC_ var).
const BACKEND_URL =
  process.env.BACKEND_URL ??
  process.env.NEXT_PUBLIC_API_URL ??
  "http://127.0.0.1:8000";

export async function POST(req: NextRequest) {
  let body: { threadId?: unknown; action?: unknown; data?: unknown };
  try {
    body = (await req.json()) as typeof body;
  } catch {
    return NextResponse.json({ error: "Malformed JSON" }, { status: 400 });
  }
  const { threadId, action, data } = body;
  if (typeof threadId !== "string" || typeof action !== "string") {
    return NextResponse.json({ error: "Missing threadId or action" }, { status: 400 });
  }

  try {
    const cookieStore = await cookies();
    const accessToken = cookieStore.get(TOKEN_COOKIE)?.value;

    const res = await fetch(
      `${BACKEND_URL}/api/v1/conversations/${threadId}/hitl`,
      {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          ...(accessToken ? { Authorization: `Bearer ${accessToken}` } : {}),
        },
        body: JSON.stringify({ action, data }),
        // An approval decision is the one request in this product that must not
        // hang silently: without a ceiling a stalled backend holds the socket
        // and the user's Approve click appears to do nothing. 30s matches the
        // ceiling lib/proxy.ts applies to every other upstream call.
        signal: AbortSignal.timeout(30_000),
      }
    );

    if (!res.ok) {
      return NextResponse.json(
        { error: `Backend error: ${res.status}` },
        { status: res.status }
      );
    }

    return NextResponse.json(await res.json());
  } catch {
    return NextResponse.json(
      { error: "Failed to reach backend" },
      { status: 503 }
    );
  }
}
