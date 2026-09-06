/**
 * Next.js 14 App Router – HITL Feedback Route
 * Sends human-in-the-loop approval/rejection back to LangGraph via FastAPI.
 */
import { NextRequest, NextResponse } from "next/server";
import { cookies } from "next/headers";
import { TOKEN_COOKIE } from "@/lib/auth";

const BACKEND_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

export async function POST(req: NextRequest) {
  const { threadId, action, data } = await req.json();

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
      }
    );

    if (!res.ok) {
      return NextResponse.json(
        { error: `Backend error: ${res.status}` },
        { status: res.status }
      );
    }

    return NextResponse.json(await res.json());
  } catch (error) {
    return NextResponse.json(
      { error: "Failed to reach backend" },
      { status: 503 }
    );
  }
}
