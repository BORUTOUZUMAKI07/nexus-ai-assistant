import { NextRequest, NextResponse } from "next/server";
import { proxyJson } from "@/lib/proxy";

/** The caller's registered webhook endpoints. */
export async function GET() {
  return proxyJson("/webhooks");
}

/** Register an endpoint; returns its HMAC signing secret once. */
export async function POST(req: NextRequest) {
  let body: unknown;
  try {
    body = await req.json();
  } catch {
    return NextResponse.json({ detail: "invalid_json_body" }, { status: 400 });
  }
  return proxyJson("/webhooks", { method: "POST", body: JSON.stringify(body) });
}