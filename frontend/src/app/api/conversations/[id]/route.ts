import { NextRequest } from "next/server";
import { proxyJson } from "@/lib/proxy";

type Params = { params: Promise<{ id: string }> };

export async function DELETE(_req: NextRequest, ctx: Params) {
  const { id } = await ctx.params;
  return proxyJson(`/conversations/${id}`, { method: "DELETE" });
}

export async function PATCH(req: NextRequest, ctx: Params) {
  const { id } = await ctx.params;
  const body = await req.json();
  return proxyJson(`/conversations/${id}`, {
    method: "PATCH",
    body: JSON.stringify(body),
  });
}