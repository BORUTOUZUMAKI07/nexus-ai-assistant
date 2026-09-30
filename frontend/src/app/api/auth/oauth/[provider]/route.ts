import { NextResponse } from "next/server";
import { backendFetch } from "@/lib/proxy";

type Ctx = { params: Promise<{ provider: string }> };

/**
 * GET /api/auth/oauth/[provider] — provider authorize-URL provider.
 *
 * Forwards to the backend OAuth start endpoint (which requires PKCE state) and
 * returns `{ authorization_url, state, provider }`. The sign-in page's
 * provider button then sends the browser to `authorization_url`; the provider
 * bounces the user back to /api/auth/oauth/{provider}/callback.
 *
 * Provider validation lives on the backend: an unknown name or a provider with
 * no client id configured answers 404, which is passed through so the page can
 * surface "single sign-on is not configured".
 */
export async function GET(_req: Request, { params }: Ctx) {
  const { provider } = await params;
  const res = await backendFetch(`/auth/oauth/${provider}`, { method: "GET" });
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