/**
 * Nexus AI – server-side proxy helper for Next.js App Router API routes.
 *
 * Every /api/* proxy reads the JWT from the incoming request cookie and
 * forwards it to FastAPI as `Authorization: Bearer <token>`, so the browser
 * never exposes the token to the backend directly.
 */
import { cookies } from "next/headers";
import { NextResponse } from "next/server";
import { TOKEN_COOKIE } from "./auth";

const BACKEND_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

export async function backendFetch(path: string, init?: RequestInit) {
  const cookieStore = await cookies();
  const accessToken = cookieStore.get(TOKEN_COOKIE)?.value;

  const headers = new Headers(init?.headers);
  const isFormBody = init?.body instanceof FormData;
  if (!isFormBody && !headers.get("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }
  if (accessToken) headers.set("Authorization", `Bearer ${accessToken}`);

  return fetch(`${BACKEND_URL}/api/v1${path}`, { ...init, headers });
}

export async function proxyJson(path: string, init?: RequestInit) {
  const res = await backendFetch(path, init);
  const text = await res.text();
  if (!text) {
    return new NextResponse(null, { status: res.status });
  }
  try {
    return NextResponse.json(JSON.parse(text), { status: res.status });
  } catch {
    return new NextResponse(text, { status: res.status });
  }
}