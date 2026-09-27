import { NextRequest } from "next/server";
import { proxyJson } from "@/lib/proxy";

type Ctx = { params: Promise<{ id: string }> };

// POST /api/plans/[id]/reject — reject a pending plan (optional reason).
export async function POST(req: NextRequest, { params }: Ctx) {
  const { id } = await params;
  let body: { reason?: string } = {};
  try {
    body = (await req.json()) as { reason?: string };
  } catch {
    // reason is optional — an empty body is valid
  }
  return proxyJson(`/plans/${id}/reject`, {
    method: "POST",
    body: JSON.stringify(body),
  });
}