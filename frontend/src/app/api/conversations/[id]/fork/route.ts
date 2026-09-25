import { NextRequest } from "next/server";
import { proxyJson } from "@/lib/proxy";

type Ctx = { params: Promise<{ id: string }> };

export async function POST(req: NextRequest, { params }: Ctx) {
  const { id } = await params;
  const body = await req.json();
  return proxyJson(`/conversations/${id}/fork`, {
    method: "POST",
    body: JSON.stringify(body),
  });
}
