import { NextRequest, NextResponse } from "next/server";
import { proxyJson } from "@/lib/proxy";

type Ctx = { params: Promise<{ id: string }> };

// PUT    /api/admin/hooks/[id] — update a hook policy.
// DELETE /api/admin/hooks/[id] — delete a hook policy.
export async function PUT(req: NextRequest, { params }: Ctx) {
  const { id } = await params;
  let body: unknown;
  try {
    body = await req.json();
  } catch {
    return NextResponse.json({ detail: "invalid_json_body" }, { status: 400 });
  }
  return proxyJson(`/admin/hooks/${id}`, {
    method: "PUT",
    body: JSON.stringify(body),
  });
}

export async function DELETE(_req: NextRequest, { params }: Ctx) {
  const { id } = await params;
  return proxyJson(`/admin/hooks/${id}`, { method: "DELETE" });
}