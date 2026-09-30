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
 *     client-side probe then shows the sign-in modal — the previous behaviour,
 *     which remains the source of truth;
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

/** Must match TOKEN_COOKIE in `src/lib/auth.ts`. */
const ACCESS_COOKIE = "nexus_access_token";

/** Where an unauthenticated visitor is sent. */
const SIGNIN_PATH = "/signin";

export function proxy(request: NextRequest) {
  const { pathname } = request.nextUrl;

  // The access cookie is httpOnly, so this runs server-side where it is
  // readable. Its mere presence is all we check — see the scope note above.
  if (!request.cookies.get(ACCESS_COOKIE)?.value) {
    const signin = new URL(SIGNIN_PATH, request.url);
    // Preserve where they were heading so signin could return them there.
    // signin currently always lands on /app, so this is informational; keeping
    // it means a future return-honouring signin needs no change here.
    if (pathname !== SIGNIN_PATH) signin.searchParams.set("next", safeNext(pathname));
    return NextResponse.redirect(signin);
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
  // Only the workspace. `/` and `/signin` must stay reachable anonymously, and
  // `/api/*` is handled by the route handlers in src/app/api.
  matcher: ["/app", "/app/:path*"],
};
