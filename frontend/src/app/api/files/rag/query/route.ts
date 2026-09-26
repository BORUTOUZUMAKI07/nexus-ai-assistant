import { NextRequest, NextResponse } from "next/server";
import { proxyJson } from "@/lib/proxy";

export async function POST(req: NextRequest) {
  let body: unknown;
  try {
    body = await req.json();
  } catch {
    return NextResponse.json({ detail: "invalid_json_body" }, { status: 400 });
  }
  return proxyJson("/files/rag/query", {
    method: "POST",
    body: JSON.stringify(body),
  });
}