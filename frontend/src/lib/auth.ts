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

export const ACCESS_TOKEN_MAX_AGE = 60 * 60 * 24 * 7; // seconds (matches backend default)
export const REFRESH_TOKEN_MAX_AGE = 60 * 60 * 24 * 7; // seconds

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
 * Best-effort document.cookie scan. With httpOnly cookies in a real browser
 * this always returns null — prefer `checkAuth()` for a truthful session check.
 */
export function getAccessToken(): string | null {
  if (typeof document === "undefined") return null;
  const match = document.cookie
    .split(";")
    .map((c) => c.trim())
    .find((c) => c.startsWith(`${TOKEN_COOKIE}=`));
  return match ? decodeURIComponent(match.slice(TOKEN_COOKIE.length + 1)) : null;
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