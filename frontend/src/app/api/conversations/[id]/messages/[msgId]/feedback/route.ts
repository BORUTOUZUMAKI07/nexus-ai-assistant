import { NextRequest } from "next/server";
import { proxyJson } from "@/lib/proxy";

export async function POST(
  req: NextRequest,
  { params }: { params: Promise<{ id: string; msgId: string }> }
) {
  const { id, msgId } = await params;
  const body = await req.text();
  return proxyJson(`/conversations/${id}/messages/${msgId}/feedback`, {
    method: "POST",
    body,
  });
}
