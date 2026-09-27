import { NextRequest } from "next/server";
import { proxyJson } from "@/lib/proxy";

type Ctx = { params: Promise<{ id: string }> };

// GET    /api/artifacts/[id] — artifact detail incl. version history.
// DELETE /api/artifacts/[id] — delete artifact + all versions.
export async function GET(_req: NextRequest, { params }: Ctx) {
  const { id } = await params;
  return proxyJson(`/artifacts/${id}`);
}

export async function DELETE(_req: NextRequest, { params }: Ctx) {
  const { id } = await params;
  return proxyJson(`/artifacts/${id}`, { method: "DELETE" });
}