import { NextRequest } from "next/server";
import { proxyJson } from "@/lib/proxy";

type Ctx = { params: Promise<{ id: string }> };

export async function GET(_req: NextRequest, { params }: Ctx) {
  const { id } = await params;
  return proxyJson(`/conversations/${id}`);
}

export async function PATCH(req: NextRequest, { params }: Ctx) {
  const { id } = await params;
  const body = await req.json();
  return proxyJson(`/conversations/${id}`, {
    method: "PATCH",
    body: JSON.stringify(body),
  });
}

export async function DELETE(_req: NextRequest, { params }: Ctx) {
  const { id } = await params;
  return proxyJson(`/conversations/${id}`, { method: "DELETE" });
}