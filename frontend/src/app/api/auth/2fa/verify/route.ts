import { cookies } from "next/headers";
import { NextRequest, NextResponse } from "next/server";
import {
  ACCESS_TOKEN_MAX_AGE,
  PREAUTH_COOKIE,
  REFRESH_COOKIE,
  REFRESH_TOKEN_MAX_AGE,
  TOKEN_COOKIE,
  authCookieOptions,
} from "@/lib/auth";
import { backendFetch } from "@/lib/proxy";

/**
 * Exchange the BFF-owned preauth challenge for a real session.
 *
 * The backend answers with a token pair on *its* domain, which the browser
 * cannot use. This route is the translation layer: it sends the hidden cookie
 * value upstream, then re-bakes both tokens as httpOnly cookies on the
 * frontend's own domain. Neither the preauth token nor the raw tokens are
 * returned to page scripts.
 */
export async function POST(req: NextRequest) {
  let body: { code?: unknown };
  try {
    body = await req.json();
  } catch {
    return NextResponse.json({ ok: false, detail: "Invalid JSON body" }, { status: 400 });
  }

  const code = typeof body?.code === "string" ? body.code.trim() : "";
  if (!code) {
    return NextResponse.json(
      { ok: false, detail: "A verification code is required." },
      { status: 400 },
    );
  }

  const cookieStore = await cookies();
  const preauthToken = cookieStore.get(PREAUTH_COOKIE)?.value;
  if (!preauthToken) {
    return NextResponse.json(
      { ok: false, detail: "The two-factor challenge expired. Sign in again." },
      { status: 401 },
    );
  }

  const res = await backendFetch("/auth/2fa/verify", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ preauth_token: preauthToken, code }),
  });

  const text = await res.text();
  if (!text) return new NextResponse(null, { status: res.status });

  let data: Record<string, unknown>;
  try {
    data = JSON.parse(text) as Record<string, unknown>;
  } catch {
    return new NextResponse(text, { status: res.status });
  }

  if (!res.ok) {
    return NextResponse.json(
      { ok: false, detail: data?.detail ?? "Two-factor verification failed." },
      { status: res.status },
    );
  }

  if (typeof data?.access_token !== "string" || !data.access_token) {
    return NextResponse.json(
      { ok: false, detail: "Two-factor verification returned no session." },
      { status: 502 },
    );
  }

  const cookieOptions = authCookieOptions(req);
  const response = NextResponse.json({ ok: true }, { status: res.status });
  response.cookies.set(TOKEN_COOKIE, data.access_token, {
    ...cookieOptions,
    maxAge: typeof data.expires_in === "number" ? data.expires_in : ACCESS_TOKEN_MAX_AGE,
  });
  if (typeof data.refresh_token === "string" && data.refresh_token) {
    response.cookies.set(REFRESH_COOKIE, data.refresh_token, {
      ...cookieOptions,
      maxAge:
        typeof data.refresh_expires_in === "number"
          ? data.refresh_expires_in
          : REFRESH_TOKEN_MAX_AGE,
    });
  }
  // The challenge has been spent, so drop the BFF cookie. A failed verification
  // leaves it in place so the user can correct a mistyped code without signing
  // in again; the backend still enforces single-use and its own attempt limit.
  response.cookies.set(PREAUTH_COOKIE, "", { ...cookieOptions, maxAge: 0 });
  return response;
}
