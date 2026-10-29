import { NextRequest } from "next/server";
import { proxyJson } from "@/lib/proxy";

type Ctx = { params: Promise<{ id: string }> };

/** Recent delivery attempts (status, HTTP code, attempts, error). */
export async function GET(req: NextRequest, { params }: Ctx) {
  const { id } = await params;
  const limit = new URL(req.url).searchParams.get("limit");
  return proxyJson(`/webhooks/${id}/deliveries${limit ? `?limit=${limit}` : ""}`);
}