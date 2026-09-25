import { NextResponse } from "next/server";
import { backendFetch } from "@/lib/proxy";

/**
 * Session probe used by the client to gate the UI. The browser cannot read the
 * httpOnly access cookie, so this route verifies it against the backend and
 * only returns `authenticated: true` when the user is genuinely signed in.
 */
export async function GET() {
  const res = await backendFetch("/auth/me", { method: "GET" });

  if (res.status === 200) {
    let user: unknown = null;
    try {
      user = await res.json();
    } catch {
      // Ignore malformed user payload; the boolean is what matters for gating.
    }
    return NextResponse.json({ authenticated: true, user }, { status: 200 });
  }

  // 401/403 and transient backend failures (503) all mean "not a session" —
  // never hard-fail the whole page on a cold-start probe.
  return NextResponse.json({ authenticated: false }, { status: 200 });
}