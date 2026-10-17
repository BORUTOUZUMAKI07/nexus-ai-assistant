/**
 * Nexus AI – client-side auth session helper.
 *
 * The JWT access + refresh tokens live ONLY in httpOnly + Secure cookies that
 * are written by the server-side auth route handlers (/api/auth/login,
 * /api/auth/refresh, /api/auth/logout). Because the cookies are httpOnly,
 * document.cookie cannot read them, so a successful XSS cannot exfiltrate raw
 * tokens. Every /api/* proxy reads the cookies server-side (lib/proxy.ts) and
 * attaches `Authorization: Bearer <token>` to the FastAPI backend itself.
 *
 * The client therefore never sees or stores tokens; it only asks the server
 * whether a session exists (`checkAuth`) or tears it down (`clearSession`).
 */

export const TOKEN_COOKIE = "nexus_access_token";
export const REFRESH_COOKIE = "nexus_refresh_token";
export const SESSION_EXPIRED_EVENT = "nexus:session-expired";

// Fallback cookie lifetimes, used only when the backend response omits the
// corresponding `expires_in` / `refresh_expires_in`. They mirror the backend
// defaults (ACCESS_TOKEN_EXPIRE_MINUTES=60, REFRESH_TOKEN_EXPIRE_DAYS=30) and
// were previously 7 days for BOTH cookies, which meant a cookie could outlive
// its own token by up to 6 days — the browser kept presenting a refresh cookie
// the backend had already expired, turning a clean expiry into a 401 loop.
export const ACCESS_TOKEN_MAX_AGE = 60 * 60; // 1 hour
export const REFRESH_TOKEN_MAX_AGE = 60 * 60 * 24 * 30; // 30 days

// Cookie attributes shared by the auth route handlers. httpOnly + Secure keep
// the JWTs out of document.cookie.
export const AUTH_COOKIE_OPTIONS = {
  httpOnly: true,
  sameSite: "lax" as const,
  path: "/",
} as const;

/**
 * Protocol-aware Secure flag. Browsers refuse to store a `Secure` cookie that
 * arrives over plain http, which silently kills the whole session on local
 * http deployments. The flag is only set when the request really came over
 * HTTPS (Vercel and other TLS-terminating proxies advertise the client's
 * original scheme through x-forwarded-proto).
 */
export function authCookieOptions(request: Request): typeof AUTH_COOKIE_OPTIONS & {
  secure: boolean;
} {
  const forwardedProto = request.headers.get("x-forwarded-proto") ?? "";
  const firstProto = forwardedProto.split(",")[0]?.trim();
  const isSecure =
    new URL(request.url).protocol === "https:" || firstProto === "https";
  return { ...AUTH_COOKIE_OPTIONS, secure: isSecure };
}

/**
 * The public origin the browser actually used, taken from the request's `Host`
 * header rather than from `request.url`.
 *
 * Why this is not just `new URL(request.url).origin`
 * ──────────────────────────────────────────────────
 * The deployed frontend is the Next.js standalone server, which binds
 * `0.0.0.0` (see `scripts/serve.js` and the runner stage of the Dockerfile).
 * Next builds a route handler's `request.url` from that *bound* host, so inside
 * the OAuth callback `request.url` is `http://0.0.0.0:3000/...` no matter what
 * the browser asked for. A redirect built as `new URL(path, request.url)` is
 * therefore answered with `Location: http://0.0.0.0:3000/...`, which is a
 * different *host* from the one the browser is on (`localhost`, or the real
 * domain in production).
 *
 * That matters because a successful OAuth exchange sets host-only httpOnly
 * session cookies on the response to the `localhost` request. A redirect to
 * `0.0.0.0` is a different host, so the browser does not send them: the user
 * arrives at `/app` unauthenticated and the whole sign-in appears broken even
 * though every backend step succeeded. The `Host` header is the browser's
 * origin and survives every proxy; `x-forwarded-host`/`x-forwarded-proto` are
 * preferred when present so a TLS-terminating proxy is honoured.
 *
 * Falls back to `request.url`'s origin when no host header exists (a synthetic
 * request, e.g. in a unit test).
 */
export function requestOrigin(request: Request): string {
  const first = (value: string | null) => value?.split(",")[0]?.trim();
  const host = first(request.headers.get("x-forwarded-host")) || request.headers.get("host");
  if (!host) return new URL(request.url).origin;
  const proto =
    request.headers.get("x-forwarded-proto")?.split(",")[0]?.trim() ||
    new URL(request.url).protocol.replace(":", "");
  return `${proto}://${host}`;
}

/**
 * Asks the server whether a session exists. The /api/auth/me route handler
 * verifies the httpOnly access cookie against the backend, so this works even
 * though the browser cannot read the cookie itself.
 *
 * Note: /api/auth/me always answers 200 — it carries the outcome in the body
 * (`authenticated`) so the route never hard-fails the page on a cold-start
 * probe. checkAuth must therefore inspect the body, not the HTTP status.
 */
export async function checkAuth(): Promise<boolean> {
  try {
    const res = await fetch("/api/auth/me", { method: "GET" });
    if (!res.ok) return false;
    const data = (await res.json().catch(() => null)) as {
      authenticated?: boolean;
    } | null;
    return data?.authenticated === true;
  } catch {
    return false;
  }
}

/**
 * Ends the session. The /api/auth/logout route handler revokes the refresh
 * token server-side and clears both httpOnly cookies (which client-side
 * JavaScript cannot touch). Best-effort: never throws.
 */
export async function clearSession(): Promise<void> {
  try {
    await fetch("/api/auth/logout", { method: "POST" });
  } catch {
    // Best-effort cleanup; the app bounces to the sign-in page regardless.
  }
}