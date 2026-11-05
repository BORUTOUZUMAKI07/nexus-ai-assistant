import { NextRequest, NextResponse } from "next/server";
import { cookies } from "next/headers";
import { backendFetch } from "@/lib/proxy";
import {
  ACCESS_TOKEN_MAX_AGE,
  REFRESH_COOKIE,
  REFRESH_TOKEN_MAX_AGE,
  TOKEN_COOKIE,
  authCookieOptions,
} from "@/lib/auth";

/**
 * Session probe used by the client to gate the UI. The browser cannot read the
 * httpOnly access cookie, so this route verifies it against the backend and
 * only returns `authenticated: true` when the user is genuinely signed in.
 *
 * It has three outcomes, and the client treats them differently:
 *
 *   200 {authenticated: true}   verified session
 *   200 {authenticated: false}  the backend rejected the credential
 *   503 {detail: "auth_unavailable"}  the backend could not answer
 *
 * The third one is why this route was rewritten twice. `backendFetch` answers
 * 503 while FastAPI cold-starts (30-60s on this stack), and reporting that as
 * `authenticated: false` signed the user out of a perfectly valid session: the
 * app showed the sign-in modal and then stayed there until a manual reload,
 * with the cookie still intact the whole time. "Cannot verify" is not a verdict,
 * and only the client can retry, so the distinction has to be in the response.
 *
 * It must never be cached: the answer changes with the session and with backend
 * liveness, and a cached `authenticated: false` would outlive the outage that
 * produced it.
 */
function json(body: unknown, status = 200) {
  return NextResponse.json(body, {
    status,
    headers: { "Cache-Control": "no-store" },
  });
}

function unavailable(upstreamStatus: number) {
  return NextResponse.json(
    { detail: "auth_unavailable", upstream_status: upstreamStatus },
    {
      status: 503,
      headers: { "Cache-Control": "no-store", "Retry-After": "2" },
    },
  );
}

async function readUser(res: Response): Promise<unknown> {
  try {
    return await res.json();
  } catch {
    // Malformed user payload: the boolean is what gates the UI.
    return null;
  }
}

/**
 * Spend one refresh attempt to turn "expired access token" into a session.
 *
 * The access cookie lives an hour and the refresh cookie thirty days, so for
 * most of a user's working day the browser presents a token the backend has
 * already expired while a perfectly valid refresh token sits next to it. The
 * edge gate in `src/proxy.ts` now lets that request through instead of
 * redirecting to /signin, and the client's API calls heal themselves through
 * `nexusFetch` — but the probe deliberately does not, because `isAuthRoute()`
 * excludes it (a probe that called `clearSession()` on a 401 would broadcast a
 * false expiry to every tab). So the healing happens here, server-side, and
 * the client sees nothing but an accurate answer.
 *
 * Returns a Response when the attempt produced an answer of its own, or null
 * when it declined (no refresh cookie, or the refresh token is dead too) and
 * the caller should report the signed-out verdict the first probe already had.
 */
async function healExpiredAccessToken(
  request: NextRequest,
): Promise<NextResponse | null> {
  const refreshToken = (await cookies()).get(REFRESH_COOKIE)?.value;
  if (!refreshToken) return null;

  const refreshed = await backendFetch("/auth/refresh", {
    method: "POST",
    body: JSON.stringify({ refresh_token: refreshToken }),
  });

  // Same rule as the first probe: an unreachable backend is not a verdict.
  if (refreshed.status >= 500) return unavailable(refreshed.status);
  // 401/403 from the refresh endpoint means the refresh token is gone or
  // revoked. That one *is* a verdict.
  if (!refreshed.ok) return null;

  const data = (await refreshed.json().catch(() => null)) as {
    access_token?: string;
    refresh_token?: string;
    expires_in?: number;
    refresh_expires_in?: number;
  } | null;
  if (!data?.access_token) return null;

  // Verify the token we just minted, not the stale one still in the cookie.
  const probe = await backendFetch(
    "/auth/me",
    { method: "GET" },
    { accessToken: data.access_token },
  );
  if (probe.status >= 500) return unavailable(probe.status);
  if (probe.status !== 200) return null;

  const response = json({ authenticated: true, user: await readUser(probe) });
  const cookieOptions = authCookieOptions(request);
  response.cookies.set(TOKEN_COOKIE, data.access_token, {
    ...cookieOptions,
    maxAge: data.expires_in ?? ACCESS_TOKEN_MAX_AGE,
  });
  // Only when the backend actually rotated it, mirroring `/api/auth/refresh`:
  // re-setting an unrotated token with a fresh lifetime would let the cookie
  // outlive the JWT inside it.
  if (data.refresh_token) {
    response.cookies.set(REFRESH_COOKIE, data.refresh_token, {
      ...cookieOptions,
      maxAge: data.refresh_expires_in ?? REFRESH_TOKEN_MAX_AGE,
    });
  }
  return response;
}

export async function GET(request: NextRequest) {
  const res = await backendFetch("/auth/me", { method: "GET" });

  if (res.status === 200) {
    return json({ authenticated: true, user: await readUser(res) });
  }

  // A rejection from the backend is the only thing that means "no session" —
  // but it may mean only that the *access* token expired, so try the refresh
  // token once before telling the client anything.
  if (res.status === 401 || res.status === 403) {
    const healed = await healExpiredAccessToken(request);
    return healed ?? json({ authenticated: false });
  }

  return unavailable(res.status);
}
