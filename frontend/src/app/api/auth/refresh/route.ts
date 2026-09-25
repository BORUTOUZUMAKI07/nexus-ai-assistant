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

export async function POST(request: NextRequest) {
  // The refresh token only ever travels inside an httpOnly cookie; it is never
  // requested from (or echoed back to) page scripts.
  const cookieStore = await cookies();
  const refreshToken = cookieStore.get(REFRESH_COOKIE)?.value;

  if (!refreshToken) {
    return NextResponse.json({ detail: "Not authenticated" }, { status: 401 });
  }

  const res = await backendFetch("/auth/refresh", {
    method: "POST",
    body: JSON.stringify({ refresh_token: refreshToken }),
  });

  const text = await res.text();
  if (!text) return new NextResponse(null, { status: res.status });
  try {
    const data = JSON.parse(text);
    const response = NextResponse.json(
      { ok: Boolean(data.access_token) },
      { status: res.status }
    );
    if (res.ok && data.access_token) {
      const cookieOptions = authCookieOptions(request);
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