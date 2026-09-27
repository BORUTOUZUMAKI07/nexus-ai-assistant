import { NextRequest, NextResponse } from "next/server";
import {
  ACCESS_TOKEN_MAX_AGE,
  REFRESH_COOKIE,
  REFRESH_TOKEN_MAX_AGE,
  TOKEN_COOKIE,
  authCookieOptions,
} from "@/lib/auth";
import { backendFetch } from "@/lib/proxy";

/**
 * GET /api/auth/oauth/callback — the IdP redirects browsers here after SSO.
 *
 * The provider ships `?code&state` on this URL. The proxy completes the
 * authorization-code + PKCE exchange with the backend, then persists the
 * session exactly like /api/auth/login: raw tokens go into httpOnly cookies,
 * and the user lands on the app.
 *
 * Degradations are deliberate and documented:
 *   * `2fa_required`  — TOTP-enabled accounts bounce to the password sign-in,
 *     which returns the same preauth challenge (the SSO page has no TOTP UI).
 *   * provider errors  — bounce to /signin with an `error` query so the page
 *     can render the message without ever leaking tokens to the browser.
 */
export async function GET(req: NextRequest) {
  const { searchParams } = new URL(req.url);
  const code = searchParams.get("code") ?? "";
  const state = searchParams.get("state") ?? "";
  if (!code || !state) {
    return NextResponse.redirect(
      new URL("/signin?error=oauth_missing_params", req.url),
    );
  }

  const res = await backendFetch("/auth/oauth/callback", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ code, state }),
  });

  const text = await res.text();
  let data: { [key: string]: unknown } | null = null;
  try {
    data = text ? (JSON.parse(text) as { [key: string]: unknown }) : null;
  } catch {
    data = null;
  }

  if (!res.ok) {
    const detail =
      data && typeof data.detail === "string"
        ? data.detail
        : `oauth_callback_failed_${res.status}`;
    return NextResponse.redirect(
      new URL(`/signin?error=${encodeURIComponent(detail)}`, req.url),
    );
  }

  if (data?.status === "2fa_required") {
    return NextResponse.redirect(
      new URL("/signin?oauth=2fa", req.url),
    );
  }

  if (data?.access_token) {
    const response = NextResponse.redirect(new URL("/app", req.url));
    const cookieOptions = authCookieOptions(req);
    response.cookies.set(TOKEN_COOKIE, String(data.access_token), {
      ...cookieOptions,
      maxAge: Number(data.expires_in) || ACCESS_TOKEN_MAX_AGE,
    });
    if (data.refresh_token) {
      response.cookies.set(REFRESH_COOKIE, String(data.refresh_token), {
        ...cookieOptions,
        maxAge: REFRESH_TOKEN_MAX_AGE,
      });
    }
    return response;
  }

  return NextResponse.redirect(new URL("/signin?error=oauth_no_token", req.url));
}