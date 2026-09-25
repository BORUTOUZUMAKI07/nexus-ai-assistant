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

const BACKEND_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://127.0.0.1:8000";

export async function backendFetch(path: string, init?: RequestInit) {
  const cookieStore = await cookies();
  const accessToken = cookieStore.get(TOKEN_COOKIE)?.value;

  const headers = new Headers(init?.headers);
  const isFormBody = init?.body instanceof FormData;
  if (!isFormBody && !headers.get("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }
  // Login/refresh are reached with a possibly expired/stale access token still
  // in the cookie; forwarding it would shadow the fresh credentials. All other
  // routes — including /auth/me, which is the app's authenticated session probe
  // — must present the bearer token or the backend rejects them as anonymous.
  const isTokenlessAuth =
    path.startsWith("/auth/login") || path.startsWith("/auth/refresh");
  if (accessToken && !isTokenlessAuth) {
    headers.set("Authorization", `Bearer ${accessToken}`);
  }

  try {
    return await fetch(`${BACKEND_URL}/api/v1${path}`, { ...init, headers });
  } catch {
    // Backend is unreachable (cold start takes 30-60s on this stack). Surface
    // a clean 503 JSON instead of letting the raw TypeError bubble up as a 500
    // with a `TypeError: fetch failed` stack in the dev console.
    return new Response(JSON.stringify({ detail: "backend_unavailable" }), {
      status: 503,
      headers: { "Content-Type": "application/json" },
    });
  }
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