/**
 * Audio Transcription Proxy – forwards multipart audio to FastAPI /api/v1/audio/transcribe
 * Uses Groq Whisper on the backend (0 MB extra RAM, free tier).
 */
import { NextRequest, NextResponse } from "next/server";
import { cookies } from "next/headers";
import { TOKEN_COOKIE } from "@/lib/auth";
import { MAX_AUDIO_UPLOAD_BYTES, rejectOversizedBody } from "@/lib/upload-limits";

export const runtime = "nodejs";

// Server-only backend address (see lib/proxy.ts — never a NEXT_PUBLIC_ var).
const BACKEND_URL =
  process.env.BACKEND_URL ??
  process.env.NEXT_PUBLIC_API_URL ??
  "http://127.0.0.1:8000";

export async function POST(req: NextRequest) {
  const cookieStore = await cookies();
  const accessToken = cookieStore.get(TOKEN_COOKIE)?.value;

  // Judged before `formData()` buffers the body into this process. Unlike the
  // file upload path there is no backend ceiling to fall back on here —
  // api/v1/audio.py does a bare `await file.read()` — so this is the only place
  // the size is bounded at all.
  const tooLarge = rejectOversizedBody(
    req.headers.get("content-length"),
    MAX_AUDIO_UPLOAD_BYTES,
    "Audio",
  );
  if (tooLarge) return tooLarge;

  // Read the multipart form data from the client
  const formData = await req.formData();
  const file = formData.get("file");
  if (!file || !(file instanceof Blob)) {
    return NextResponse.json({ detail: "No audio file provided" }, { status: 400 });
  }

  // Re-build a FormData to forward to FastAPI
  const backendForm = new FormData();
  backendForm.append("file", file, (file as File).name ?? "recording.webm");

  try {
    const res = await fetch(`${BACKEND_URL}/api/v1/audio/transcribe`, {
      method: "POST",
      headers: {
        ...(accessToken ? { Authorization: `Bearer ${accessToken}` } : {}),
      },
      body: backendForm,
      // Transcription is billable and slow, so it gets a longer ceiling than a
      // JSON round-trip -- but not an infinite one. Without a signal, a backend
      // that accepts the upload and stalls holds the socket and the request
      // never returns, so the recording is silently lost and the call is never
      // billed. 120s is generous for a short voice note.
      signal: AbortSignal.timeout(120_000),
    });

    if (!res.ok) {
      const detail = await res.text();
      return NextResponse.json({ detail }, { status: res.status });
    }

    const data = (await res.json()) as { text: string };
    return NextResponse.json(data);
  } catch (err) {
    // Do not forward the raw error message. Every sibling route returns a
    // generic string here, and this one was the only place leaking an internal
    // `err.message` -- inconsistent even though the practical content is just
    // "fetch failed". Report the timeout distinctly so a slow backend is not
    // mistaken for an unreachable one.
    const timedOut = err instanceof Error && err.name === "TimeoutError";
    return NextResponse.json(
      { detail: timedOut ? "Transcription timed out" : "Transcription proxy error" },
      { status: timedOut ? 504 : 502 }
    );
  }
}
