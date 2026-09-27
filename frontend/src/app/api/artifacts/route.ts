import { NextRequest, NextResponse } from "next/server";
import { proxyJson } from "@/lib/proxy";

// GET  /api/artifacts  — list own artifacts (optional ?conversation_id=&limit=).
// POST /api/artifacts  — persist a new artifact.
export async function GET(req: NextRequest) {
  const { searchParams } = new URL(req.url);
  const query = searchParams.toString();
  return proxyJson(`/artifacts${query ? `?${query}` : ""}`);
}

export async function POST(req: NextRequest) {
  let body: unknown;
  try {
    body = await req.json();
  } catch {
    return NextResponse.json({ detail: "invalid_json_body" }, { status: 400 });
  }
  return proxyJson("/artifacts", {
    method: "POST",
    body: JSON.stringify(body),
  });
}