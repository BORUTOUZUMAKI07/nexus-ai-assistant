/**
 * Text-to-speech proxy -> FastAPI GET /api/v1/audio/speech.
 *
 * The backend synthesizes through edge-tts (Microsoft Edge neural voices):
 * free, key-less, no billing. The bytes are streamed back with the backend's
 * own media type so the client can hand them straight to an `<audio>` element.
 *
 * Uses `fetch` directly rather than `backendFetch` for two reasons: it returns
 * binary, not JSON (so `proxyJson` cannot be used), and synthesis of a long
 * paragraph can outlast the shared helper's 30s JSON ceiling.
 */
import { NextRequest, NextResponse } from "next/server";
import { cookies } from "next/headers";
import { TOKEN_COOKIE } from "@/lib/auth";

export const runtime = "nodejs";
export const maxDuration = 60;

const BACKEND_URL =
  process.env.BACKEND_URL ??
  process.env.NEXT_PUBLIC_API_URL ??
  "http://127.0.0.1:8000";

export async function GET(req: NextRequest) {
  const cookieStore = await cookies();
  const accessToken = cookieStore.get(TOKEN_COOKIE)?.value;

  const { searchParams } = new URL(req.url);
  const text = searchParams.get("text") ?? "";
  const voice = searchParams.get("voice");
  if (!text.trim()) {
    return NextResponse.json({ detail: "text_required" }, { status: 400 });
  }

  const params = new URLSearchParams({ text });
  if (voice) params.set("voice", voice);

  try {
    const res = await fetch(`${BACKEND_URL}/api/v1/audio/speech?${params}`, {
      headers: {
        ...(accessToken ? { Authorization: `Bearer ${accessToken}` } : {}),
      },
      signal: AbortSignal.timeout(60_000),
    });

    if (!res.ok) {
      let detail = "speech_synthesis_failed";
      try {
        detail = (await res.clone().json()).detail ?? detail;
      } catch {
        /* non-JSON error body: keep the generic detail */
      }
      return NextResponse.json({ detail }, { status: res.status });
    }

    const audio = await res.arrayBuffer();
    return new NextResponse(audio, {
      status: 200,
      headers: {
        "Content-Type": res.headers.get("content-type") ?? "audio/mpeg",
        "Content-Length": String(audio.byteLength),
        "Cache-Control": "no-store",
        "X-Content-Type-Options": "nosniff",
      },
    });
  } catch (err) {
    const timedOut = err instanceof Error && err.name === "TimeoutError";
    return NextResponse.json(
      { detail: timedOut ? "speech_synthesis_timeout" : "speech_synthesis_unreachable" },
      { status: timedOut ? 504 : 503 }
    );
  }
}
