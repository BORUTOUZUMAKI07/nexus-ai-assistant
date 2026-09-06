import { NextRequest } from "next/server";
import { proxyJson } from "@/lib/proxy";

type Params = { params: Promise<{ id: string }> };

export async function DELETE(_req: NextRequest, ctx: Params) {
  const { id } = await ctx.params;
  return proxyJson(`/files/${id}`, { method: "DELETE" });
}