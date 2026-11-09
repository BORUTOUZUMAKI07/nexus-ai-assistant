import { NextRequest, NextResponse } from "next/server";
import { backendFetch } from "@/lib/proxy";

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

  // Forward the backend's Set-Cookie headers (httpOnly tokens) to the client.
  // The backend sets both cookies; we just echo them through so the browser
  // stores them and sends them back on every subsequent request.
  const response = NextResponse.json(
    { ok: true },
    { status: res.status, headers: res.headers }
  );
  return response;
}