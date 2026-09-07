import { NextRequest } from "next/server";
import { proxyJson } from "@/lib/proxy";

type Ctx = { params: Promise<{ id: string }> };

export async function DELETE(_req: NextRequest, { params }: Ctx) {
  const { id } = await params;
  return proxyJson(`/settings/memories/${id}`, { method: "DELETE" });
}