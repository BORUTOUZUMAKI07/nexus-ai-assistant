/**
 * Nexus AI – server-side proxy helper for Next.js App Router API routes.
 *
 * Every /api/* proxy reads the JWT from the incoming request cookie and
 * forwards it to FastAPI as `Authorization: Bearer <token>`, so the browser
 * never exposes the token to the backend directly.
 */
import "server-only";

import { cookies, headers as nextHeaders } from "next/headers";
import { NextResponse } from "next/server";
import { TOKEN_COOKIE } from "./auth";

// Server-only backend address. Do NOT use a NEXT_PUBLIC_ variable here: this
// value must never reach the client bundle (it exposes the internal host/port
// and bakes the address in at build time). BACKEND_URL is read from the
// server environment at request time; the NEXT_PUBLIC_API_URL fallback keeps
// older local setups working.
const BACKEND_URL =
  process.env.BACKEND_URL ??
  process.env.NEXT_PUBLIC_API_URL ??
  "http://127.0.0.1:8000";

/**
 * Ceiling for a single JSON round-trip to FastAPI.
 *
 * Without this, a backend that accepts the connection and then stalls holds a
 * Node request slot open indefinitely — the socket is never released and the
 * request just hangs. That is not hypothetical here: this stack's cold start
 * takes 30-60s, and the client already retries with exponential backoff, so one
 * user action could otherwise pin up to six concurrent sockets per call.
 *
 * 30s sits above the documented cold-start window and below any reverse
 * proxy's own idle timeout, so the request fails fast and legibly instead of
 * being severed later with no useful error. The streaming chat route is
 * deliberately excluded: an answer legitimately outlives any fixed ceiling, so
 * it gets a separate, much larger budget (see `app/api/chat/route.ts`).
 */
const UPSTREAM_TIMEOUT_MS = 30_000;

/** Verbs that can change server state and therefore need an origin check. */
const MUTATING_METHODS = new Set(["POST", "PUT", "PATCH", "DELETE"]);

/**
 * Rejects cross-origin state-changing requests.
 *
 * This app has zero Server Actions, so the Origin-vs-Host check that Next
 * applies to Server Actions never runs — the protection the framework gives you
 * for free is switched off by the architecture. What is actually holding the
 * line today is the `SameSite=Lax` auth cookie, which stops a cross-site POST
 * from carrying credentials.
 *
 * That is a single browser default with nothing behind it. The one routine
 * change that removes it -- flipping `sameSite` to `"none"` so sign-in works
 * across subdomains or in an iframe -- would open all 43 handlers to CSRF
 * silently, with no test to catch it. Comparing Origin to the expected host is
 * the same check Next performs for Server Actions, applied here so the invariant
 * is enforced by code rather than by a cookie attribute.
 *
 * Requests with no `Origin` header are allowed through: browsers omit it for
 * same-origin navigations and for non-CORS clients, and rejecting those would
 * break the server's own fetches and any legitimate tooling.
 */
async function isCrossOriginRequest(requestHeaders: Headers): Promise<boolean> {
  const origin = requestHeaders.get("origin");
  if (!origin) return false;

  const host =
    requestHeaders.get("x-forwarded-host") ?? requestHeaders.get("host");
  if (!host) return false;

  let originHost: string;
  try {
    originHost = new URL(origin).host;
  } catch {
    // An unparseable Origin is not a real origin. Treat it as same-origin
    // rather than as an attack, so a malformed value from some proxy cannot be
    // used to lock a user out of their own app.
    return false;
  }
  return originHost !== host;
}

export async function backendFetch(path: string, init?: RequestInit) {
  const cookieStore = await cookies();
  const accessToken = cookieStore.get(TOKEN_COOKIE)?.value;

  const method = (init?.method ?? "GET").toUpperCase();
  if (MUTATING_METHODS.has(method)) {
    const requestHeaders = await nextHeaders();
    if (await isCrossOriginRequest(requestHeaders)) {
      return new Response(
        JSON.stringify({ detail: "cross_origin_request_rejected" }),
        { status: 403, headers: { "Content-Type": "application/json" } },
      );
    }
  }

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
    return await fetch(`${BACKEND_URL}/api/v1${path}`, {
      ...init,
      headers,
      signal: init?.signal ?? AbortSignal.timeout(UPSTREAM_TIMEOUT_MS),
    });
  } catch (err) {
    // A timeout is a real, reportable condition, and it must be distinguishable
    // from "the backend is not running" so the 503 does not send an operator
    // hunting for a down service when the service is actually just slow.
    const timedOut = err instanceof Error && err.name === "TimeoutError";
    // Backend is unreachable (cold start takes 30-60s on this stack). Surface
    // a clean 503 JSON instead of letting the raw TypeError bubble up as a 500
    // with a `TypeError: fetch failed` stack in the dev console.
    return new Response(
      JSON.stringify({ detail: timedOut ? "backend_timeout" : "backend_unavailable" }),
      {
        status: 503,
        headers: {
          "Content-Type": "application/json",
          ...(timedOut ? { "Retry-After": "5" } : {}),
        },
      },
    );
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