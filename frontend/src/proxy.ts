/**
 * Edge guard for the signed-in workspace.
 *
 * Why this exists
 * ───────────────
 * `/app` was gated in the browser only. The page shipped its full HTML and
 * JavaScript bundle to every visitor, and only after hydration did
 * `app/page.tsx` probe `/api/auth/me` and pop the sign-in modal. So the
 * workspace source was served to anonymous users, and the sign-in screen
 * arrived late with a flash of unusable UI.
 *
 * This moves the first gate to the server. A request for `/app` with no session
 * cookie is redirected to `/signin` before any of that is rendered.
 *
 * Scope — this is an OPTIMISTIC check, not the security boundary
 * ────────────────────────────────────────────────────────────────
 * All it checks is whether a cookie is present. It cannot tell a valid token
 * from an expired or forged one without the signing secret, so:
 *
 *   * a request that carries a stale cookie still renders the shell, and the
 *     client-side probe decides: a live refresh token heals the session in
 *     `/api/auth/me`, and only a real rejection shows the sign-in modal — the
 *     previous behaviour, which remains the source of truth;
 *   * every API route is already authorised server-side in `lib/proxy.ts`, which
 *     forwards the cookie to FastAPI for verification.
 *
 * This is deliberately the "optimistic check with Proxy" pattern from the
 * Next.js docs, which notes that Proxy is not a substitute for real session
 * management. It removes wasted bytes and the UI flash; it does not replace
 * server-side authorisation.
 *
 * Note: in Next.js 16 this file is `proxy.ts` — `middleware.ts` was renamed.
 */
import { NextResponse, type NextRequest } from "next/server";

/** Must match TOKEN_COOKIE / REFRESH_COOKIE in `src/lib/auth.ts`. */
const ACCESS_COOKIE = "nexus_access_token";
const REFRESH_COOKIE = "nexus_refresh_token";

/** Where an unauthenticated visitor is sent. */
const SIGNIN_PATH = "/signin";

/** Where a visitor with a session belongs. */
const APP_HOME = "/app";

/**
 * Query flags that must still reach /signin even when a session exists.
 *
 * Every one of them is a *result* the page has to render rather than the start
 * of a new sign-in: an OAuth `code` still being exchanged, a provider or
 * callback `error`, the TOTP bounce (`oauth=2fa`), and the expired-session flag.
 * Bouncing those to the workspace would swallow the only explanation the user
 * is given for a sign-in that just failed.
 */
const SIGNIN_RESULT_PARAMS = ["code", "error", "oauth", "message"] as const;

export function proxy(request: NextRequest) {
  const { pathname } = request.nextUrl;

  // The cookies are httpOnly, so this runs server-side where they are
  // readable. Their mere presence is all we check — see the scope note above.
  //
  // Either one counts, and that is not a loosening: the access token lives one
  // hour while the refresh token lives thirty days, so gating on the access
  // cookie alone bounced a normal working day to /signin — measured: a tab with
  // a valid 30-day refresh cookie was redirected to /signin an hour after
  // signing in, and stayed there even though `/api/auth/me` would have healed
  // the session on the next request. Any cookie present means "a session may
  // exist", which is exactly what an optimistic gate should ask; whether it is
  // valid is decided by the probe, and by the backend for every real API call.
  const hasAccess = Boolean(request.cookies.get(ACCESS_COOKIE)?.value);
  const hasRefresh = Boolean(request.cookies.get(REFRESH_COOKIE)?.value);
  // The sign-in page itself is exempt from the redirect below: it is where an
  // anonymous visitor is *sent*, so redirecting it to itself would be a loop.
  if (!hasAccess && !hasRefresh && pathname !== SIGNIN_PATH) {
    const signin = new URL(SIGNIN_PATH, request.url);
    // Preserve where they were heading so signin could return them there.
    // signin currently always lands on /app, so this is informational; keeping
    // it means a future return-honouring signin needs no change here.
    if (pathname !== SIGNIN_PATH) signin.searchParams.set("next", safeNext(pathname));
    return NextResponse.redirect(signin);
  }

  // A visitor who already has a session does not get the sign-in form. Without
  // this, pressing "Continue with Google/GitHub" while signed in starts a
  // second consent round trip on top of a live one: the provider asks again for
  // an account already chosen, and a provider mismatch lands the user on an
  // error page for a session that was never broken. (`/login` behaves this way
  // in the url-shortner project; this is the same rule, with the result params
  // carved out so a *failed* attempt can still explain itself.)
  const hasSession = hasAccess || hasRefresh;
  const isResultOfAnAttempt = SIGNIN_RESULT_PARAMS.some((param) =>
    request.nextUrl.searchParams.has(param),
  );
  if (hasSession && pathname === SIGNIN_PATH && !isResultOfAnAttempt) {
    return NextResponse.redirect(new URL(APP_HOME, request.url));
  }

  return NextResponse.next();
}

/**
 * Constrain the `next` handoff to a path on this origin.
 *
 * `pathname` is read off `request.nextUrl`, so today it is always a clean
 * same-origin path and cannot be attacker-controlled. That is an accident of
 * *this* call site, not a property of the value, and it is exactly the kind of
 * accident that a well-meaning refactor removes:
 *
 *   - if this ever switches to reading `request.nextUrl.search`, or to
 *     `request.url` minus origin, or starts composing the value from more than
 *     one part, `//evil.com` and `https://evil.com` become expressible;
 *   - `?next=` is the classic open-redirect payload, and `/signin` is the single
 *     place in this app where such a value would be consumed as a destination.
 *
 * Returning `""` on anything that is not a plain leading-slash path makes the
 * hazard structurally impossible instead of merely absent, and costs one regex.
 * The fallbacks are ordered so a protocol-relative `//host` is rejected: it does
 * start with `/`, so a naive `startsWith("/")` check would wave it straight
 * through, and browsers treat `//host` as an absolute URL.
 */
function safeNext(pathname: string): string {
  if (!pathname.startsWith("/")) return "";
  // Reject backslashes too: several browsers normalise `/\evil.com` to
  // `//evil.com`, which is the same open redirect by another spelling.
  if (pathname.startsWith("//") || pathname.includes("\\")) return "";
  // Reject anything that could be parsed as a URL with a scheme of its own.
  if (/^\/+[a-z][a-z0-9+.-]*:/i.test(pathname)) return "";
  return pathname;
}

export const config = {
  // The workspace, plus the sign-in page itself: the latter is matched only so a
  // visitor who already has a session can be sent to the workspace instead of
  // being shown the form again — it stays reachable anonymously (the redirect
  // above exempts it), and `/api/*` is handled by the route handlers.
  matcher: ["/app", "/app/:path*", "/signin"],
};
