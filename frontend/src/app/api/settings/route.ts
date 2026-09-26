import { NextRequest, NextResponse } from "next/server";
import { proxyJson } from "@/lib/proxy";

export async function GET() {
  return proxyJson("/settings");
}

export async function PUT(req: NextRequest) {
  let body: unknown;
  try {
    body = await req.json();
  } catch {
    return NextResponse.json({ detail: "invalid_json_body" }, { status: 400 });
  }
  return proxyJson("/settings", {
    method: "PUT",
    body: JSON.stringify(body),
  });
}