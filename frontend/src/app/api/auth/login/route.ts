import { NextRequest } from "next/server";
import { backendFetch } from "@/lib/proxy";
import { NextResponse } from "next/server";

export async function POST(req: NextRequest) {
  const { email, password } = await req.json();

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
    return NextResponse.json(JSON.parse(text), { status: res.status });
  } catch {
    return new NextResponse(text, { status: res.status });
  }
}