import { NextRequest } from "next/server";
import { proxyJson } from "@/lib/proxy";

type Ctx = { params: Promise<{ id: string }> };

/** Give up membership of an organization the user does not own. */
export async function POST(_req: NextRequest, { params }: Ctx) {
  const { id } = await params;
  return proxyJson(`/orgs/${id}/leave`, { method: "POST" });
}