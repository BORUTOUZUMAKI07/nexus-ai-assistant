import { NextRequest } from "next/server";
import { proxyJson } from "@/lib/proxy";

type Ctx = { params: Promise<{ id: string }> };

// GET /api/conversations/[id]/plans — list the user's plans for a conversation.
export async function GET(_req: NextRequest, { params }: Ctx) {
  const { id } = await params;
  return proxyJson(`/conversations/${id}/plans`);
}