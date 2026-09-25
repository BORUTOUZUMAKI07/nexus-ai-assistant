import { NextRequest, NextResponse } from "next/server";
import { cookies } from "next/headers";
import { backendFetch } from "@/lib/proxy";
import { REFRESH_COOKIE, TOKEN_COOKIE, authCookieOptions } from "@/lib/auth";

export async function POST(request: NextRequest) {
  const cookieStore = await cookies();
  const refreshToken = cookieStore.get(REFRESH_COOKIE)?.value;

  // Revoke the refresh token server-side (best-effort). The token never leaves
  // the server side of this stack.
  try {
    await backendFetch("/auth/logout", {
      method: "POST",
      ...(refreshToken
        ? { body: JSON.stringify({ refresh_token: refreshToken }) }
        : {}),
    });
  } catch {
    // Never fail logout even if the backend is temporarily unreachable.
  }

  const response = NextResponse.json(
    { message: "Logged out successfully" },
    { status: 200 }
  );

  // Clear both httpOnly cookies. Clearing must use the same attributes
  // (path, secure, httpOnly) the browser stored them with.
  const cookieOptions = authCookieOptions(request);
  response.cookies.set(TOKEN_COOKIE, "", { ...cookieOptions, maxAge: 0 });
  response.cookies.set(REFRESH_COOKIE, "", { ...cookieOptions, maxAge: 0 });

  return response;
}