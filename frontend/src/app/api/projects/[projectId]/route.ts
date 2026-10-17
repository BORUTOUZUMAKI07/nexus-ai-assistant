import { NextRequest } from "next/server";
import { proxyJson } from "@/lib/proxy";

type Ctx = { params: Promise<{ projectId: string }> };

export async function PATCH(req: NextRequest, { params }: Ctx) {
  const { projectId } = await params;
  let body: unknown;
  try {
    body = await req.json();
  } catch {
    body = {};
  }
  return proxyJson(`/projects/${projectId}`, {
    method: "PATCH",
    body: JSON.stringify(body),
  });
}

export async function DELETE(_req: NextRequest, { params }: Ctx) {
  const { projectId } = await params;
  return proxyJson(`/projects/${projectId}`, { method: "DELETE" });
}
