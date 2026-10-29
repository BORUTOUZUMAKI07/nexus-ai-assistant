import { NextRequest, NextResponse } from "next/server";
import { proxyJson } from "@/lib/proxy";

/** Organizations the signed-in user belongs to. */
export async function GET() {
  return proxyJson("/orgs");
}

/** Create an organization; the caller becomes its owner. */
export async function POST(req: NextRequest) {
  let body: unknown;
  try {
    body = await req.json();
  } catch {
    return NextResponse.json({ detail: "invalid_json_body" }, { status: 400 });
  }
  return proxyJson("/orgs", { method: "POST", body: JSON.stringify(body) });
}