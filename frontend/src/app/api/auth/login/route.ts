import { NextRequest, NextResponse } from "next/server";
import { backendFetch } from "@/lib/proxy";
import {
  ACCESS_TOKEN_MAX_AGE,
  REFRESH_COOKIE,
  REFRESH_TOKEN_MAX_AGE,
  TOKEN_COOKIE,
  authCookieOptions,
} from "@/lib/auth";

export async function POST(req: NextRequest) {
  let body: { email?: unknown; password?: unknown };
  try {
    body = await req.json();
  } catch {
    // Malformed JSON must not crash the route into a 500.
    return NextResponse.json({ detail: "Invalid JSON body" }, { status: 400 });
  }
  const { email, password } = body;

  // Backend login uses OAuth2 password form (username/password), so translate
  // the JSON payload from the client into x-www-form-urlencoded.
  const form = new URLSearchParams();
  form.set("username", String(email ?? ""));
  form.set("password", String(password ?? ""));

  const res = await backendFetch("/auth/login", {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body: form.toString(),
  });

  const text = await res.text();
  if (!text) return new NextResponse(null, { status: res.status });
  try {
    const data = JSON.parse(text);
    // Only a boolean is echoed back to the browser — raw tokens stay in
    // httpOnly cookies so no page script can ever read them.
    const response = NextResponse.json(
      { ok: Boolean(data.access_token) },
      { status: res.status }
    );
    if (res.ok && data.access_token) {
      const cookieOptions = authCookieOptions(req);
      response.cookies.set(TOKEN_COOKIE, data.access_token, {
        ...cookieOptions,
        maxAge: data.expires_in ?? ACCESS_TOKEN_MAX_AGE,
      });
      if (data.refresh_token) {
        response.cookies.set(REFRESH_COOKIE, data.refresh_token, {
          ...cookieOptions,
          maxAge: REFRESH_TOKEN_MAX_AGE,
        });
      }
    }
    return response;
  } catch {
    return new NextResponse(text, { status: res.status });
  }
}