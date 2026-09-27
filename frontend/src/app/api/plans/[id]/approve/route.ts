import { NextRequest } from "next/server";
import { proxyJson } from "@/lib/proxy";

type Ctx = { params: Promise<{ id: string }> };

// POST /api/plans/[id]/approve — approve a pending plan.
export async function POST(_req: NextRequest, { params }: Ctx) {
  const { id } = await params;
  return proxyJson(`/plans/${id}/approve`, { method: "POST" });
}