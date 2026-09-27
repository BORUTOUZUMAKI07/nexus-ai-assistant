import { NextRequest, NextResponse } from "next/server";
import { proxyJson } from "@/lib/proxy";

type Ctx = { params: Promise<{ id: string }> };

// POST /api/artifacts/[id]/versions — write a new version (keeps prior content).
export async function POST(req: NextRequest, { params }: Ctx) {
  const { id } = await params;
  let body: unknown;
  try {
    body = await req.json();
  } catch {
    return NextResponse.json({ detail: "invalid_json_body" }, { status: 400 });
  }
  return proxyJson(`/artifacts/${id}/versions`, {
    method: "POST",
    body: JSON.stringify(body),
  });
}