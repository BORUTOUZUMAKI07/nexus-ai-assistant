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

/**
 * Ceiling for the approval round-trip.
 *
 * This one request is unlike every other upstream call: approving resumes the
 * parked graph **inline**, so the backend runs the remaining graph — the
 * approved tool (E2B sandbox) and then the synthesizer — before it responds.
 * A healthy resume is tens of seconds, which the 30s ceiling `lib/proxy.ts`
 * applies to ordinary JSON round-trips does not cover.
 *
 * Timing out here is not a graceful degradation, either: the backend keeps
 * going and persists the answer while the client shows an error and keeps a
 * card whose thread is no longer parked, so a retry answers "not waiting for an
 * approval". The ceiling therefore has to fit a real resume. `tool_node` is
 * budgeted 180s and the synthesizer 300s, so no finite ceiling covers the
 * absolute worst case; 150s sits above the realistic worst case (sandbox +
 * one synthesis) while still failing a genuinely wedged backend rather than
 * hanging forever.
 */
const HITL_RESUME_TIMEOUT_MS = 150_000;

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
        // and the user's Approve click appears to do nothing. See
        // HITL_RESUME_TIMEOUT_MS for why this ceiling is larger than
        // lib/proxy.ts's 30s for ordinary calls.
        signal: AbortSignal.timeout(HITL_RESUME_TIMEOUT_MS),
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
