import { NextRequest, NextResponse } from "next/server";
import { proxyJson } from "@/lib/proxy";

export async function POST(req: NextRequest) {
  let body: unknown;
  try {
    body = await req.json();
  } catch {
    // Malformed JSON must not crash the route into a 500.
    return NextResponse.json({ detail: "Invalid JSON body" }, { status: 400 });
  }
  return proxyJson("/auth/register", {
    method: "POST",
    body: JSON.stringify(body),
  });
}