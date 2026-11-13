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

  // Forward the backend's response headers, minus the ones that describe the
  // backend's *body*.
  //
  // This used to hand `res.headers` to NextResponse.json untouched, so the
  // response also carried the backend's `Content-Length: 51` and
  // `Content-Encoding: br` while the body written here is 11 bytes and
  // uncompressed. The browser sat waiting for 40 bytes that could never arrive,
  // and every sign-in hung until the client gave up -- on success and failure
  // alike. `set-cookie` stays: that is the whole point of this route, and Next
  // parses it into the response cookie jar.
  const headers = new Headers(res.headers);
  for (const framing of [
    "content-length",
    "content-encoding",
    "transfer-encoding",
    "connection",
    "keep-alive",
  ]) {
    headers.delete(framing);
  }

  if (!res.ok) {
    // Preserve the backend's own message ("Incorrect email or password") so the
    // form can show it instead of a generic line.
    let detail: unknown = "Authentication failed";
    try {
      detail = JSON.parse(text)?.detail ?? detail;
    } catch {
      // Non-JSON error body: keep the generic message.
    }
    return NextResponse.json({ ok: false, detail }, { status: res.status, headers });
  }

  // A 2FA-enabled account gets `{"status":"2fa_required","preauth_token":...}`
  // and no cookies. Passing that through keeps it distinguishable from a real
  // session instead of reporting a login the browser does not actually have.
  let payload: Record<string, unknown> = { ok: true };
  try {
    const parsed = JSON.parse(text);
    if (parsed?.status) payload = { ...parsed, ok: false };
  } catch {
    // Non-JSON success body: `{ok: true}` is the whole contract.
  }

  return NextResponse.json(payload, { status: res.status, headers });
}
