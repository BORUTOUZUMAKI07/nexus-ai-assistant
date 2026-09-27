import { NextRequest, NextResponse } from "next/server";
import { proxyJson } from "@/lib/proxy";

type Ctx = { params: Promise<{ id: string }> };

// POST /api/conversations/[id]/plan — draft a plan (plan-then-approve).
// GET  /api/conversations/[id]/plans — list plans for the conversation.
export async function POST(req: NextRequest, { params }: Ctx) {
  const { id } = await params;
  let body: unknown;
  try {
    body = await req.json();
  } catch {
    return NextResponse.json({ detail: "invalid_json_body" }, { status: 400 });
  }
  return proxyJson(`/conversations/${id}/plan`, {
    method: "POST",
    body: JSON.stringify(body),
  });
}