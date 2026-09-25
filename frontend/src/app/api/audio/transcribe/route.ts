/**
 * Audio Transcription Proxy – forwards multipart audio to FastAPI /api/v1/audio/transcribe
 * Uses Groq Whisper on the backend (0 MB extra RAM, free tier).
 */
import { NextRequest, NextResponse } from "next/server";
import { cookies } from "next/headers";
import { TOKEN_COOKIE } from "@/lib/auth";

export const runtime = "nodejs";

const BACKEND_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://127.0.0.1:8000";

export async function POST(req: NextRequest) {
  const cookieStore = await cookies();
  const accessToken = cookieStore.get(TOKEN_COOKIE)?.value;

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
    });

    if (!res.ok) {
      const detail = await res.text();
      return NextResponse.json({ detail }, { status: res.status });
    }

    const data = (await res.json()) as { text: string };
    return NextResponse.json(data);
  } catch (err) {
    const msg = err instanceof Error ? err.message : "Transcription proxy error";
    return NextResponse.json({ detail: msg }, { status: 502 });
  }
}
