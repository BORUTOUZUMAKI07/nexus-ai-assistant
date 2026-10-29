import { NextRequest, NextResponse } from "next/server";
import { proxyJson } from "@/lib/proxy";

type Ctx = { params: Promise<{ id: string }> };

/** Activate/deactivate an endpoint (`{ "is_active": bool }`). */
export async function PATCH(req: NextRequest, { params }: Ctx) {
  const { id } = await params;
  let body: unknown;
  try {
    body = await req.json();
  } catch {
    return NextResponse.json({ detail: "invalid_json_body" }, { status: 400 });
  }
  return proxyJson(`/webhooks/${id}`, {
    method: "PATCH",
    body: JSON.stringify(body),
  });
}

/** Remove an endpoint (its delivery history goes with it). */
export async function DELETE(_req: NextRequest, { params }: Ctx) {
  const { id } = await params;
  return proxyJson(`/webhooks/${id}`, { method: "DELETE" });
}