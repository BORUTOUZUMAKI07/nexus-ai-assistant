import { NextRequest, NextResponse } from "next/server";
import { proxyJson } from "@/lib/proxy";

// GET  /api/admin/hooks — list lifecycle hook policies.
// POST /api/admin/hooks — create a hook policy (hot-reloads the registry).
export async function GET() {
  return proxyJson("/admin/hooks");
}

export async function POST(req: NextRequest) {
  let body: unknown;
  try {
    body = await req.json();
  } catch {
    return NextResponse.json({ detail: "invalid_json_body" }, { status: 400 });
  }
  return proxyJson("/admin/hooks", {
    method: "POST",
    body: JSON.stringify(body),
  });
}