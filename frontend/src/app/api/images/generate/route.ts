/**
 * Image-generation proxy -> FastAPI /api/v1/images/generate.
 *
 * Free and key-less: the backend renders through Pollinations.ai and returns a
 * base64 data URL, so the browser never reaches a third-party origin itself and
 * the image is still there when the network drops.
 *
 * Uses `fetch` directly rather than the shared `backendFetch`/`proxyJson`
 * helper: that helper caps a JSON round-trip at 30s, and the first render of a
 * novel prompt routinely takes longer (the backend's own provider ceiling is
 * 60s). The signal here sits above that with headroom, matching the audio
 * route's treatment of a slow-but-billable upstream.
 */
import { NextRequest, NextResponse } from "next/server";
import { cookies } from "next/headers";
import { TOKEN_COOKIE } from "@/lib/auth";

export const runtime = "nodejs";
export const maxDuration = 120;

const BACKEND_URL =
  process.env.BACKEND_URL ??
  process.env.NEXT_PUBLIC_API_URL ??
  "http://127.0.0.1:8000";

export async function POST(req: NextRequest) {
  const cookieStore = await cookies();
  const accessToken = cookieStore.get(TOKEN_COOKIE)?.value;

  let body: unknown;
  try {
    body = await req.json();
  } catch {
    return NextResponse.json({ detail: "invalid_json_body" }, { status: 400 });
  }

  try {
    const res = await fetch(`${BACKEND_URL}/api/v1/images/generate`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        ...(accessToken ? { Authorization: `Bearer ${accessToken}` } : {}),
      },
      body: JSON.stringify(body),
      signal: AbortSignal.timeout(120_000),
    });

    const text = await res.text();
    if (!res.ok) {
      // Forward the backend's status and its own detail string (an operator
      // needs "pollinations/flux returned 502", not a generic message). The
      // body is parsed defensively because an error path is exactly where a
      // non-JSON body is most likely.
      let detail = "image_generation_failed";
      try {
        detail = (JSON.parse(text) as { detail?: string }).detail ?? detail;
      } catch {
        /* non-JSON body: keep the generic detail */
      }
      return NextResponse.json({ detail }, { status: res.status });
    }

    return NextResponse.json(JSON.parse(text));
  } catch (err) {
    // Distinguish a slow upstream from an unreachable one, so a timeout does
    // not send an operator looking for a down service.
    const timedOut = err instanceof Error && err.name === "TimeoutError";
    return NextResponse.json(
      {
        detail: timedOut
          ? "image_generation_timeout"
          : "image_generation_unreachable",
      },
      { status: timedOut ? 504 : 503 }
    );
  }
}
