import { NextResponse } from "next/server";
import { backendFetch } from "@/lib/proxy";

/**
 * GET /api/auth/oauth — SSO authorize-URL provider.
 *
 * Forwards to the backend OIDC endpoint (which requires PKCE state) and returns
 * `{ authorization_url, state, provider }`. The sign-in page's "Continue with
 * SSO" button then sends the browser to `authorization_url`; the provider
 * bounces the user back to /api/auth/oauth/callback.
 *
 * When the backend has no OAUTH_CLIENT_ID configured it answers 404, which is
 * passed through so the page can surface "single sign-on is not configured".
 */
export async function GET() {
  const res = await backendFetch("/auth/oauth/login", { method: "GET" });
  const text = await res.text();
  if (!text) {
    return NextResponse.json({ detail: "sso_unavailable" }, { status: res.status });
  }
  try {
    return NextResponse.json(JSON.parse(text), { status: res.status });
  } catch {
    return new NextResponse(text, { status: res.status });
  }
}