import { NextRequest } from "next/server";
import { proxyJson } from "@/lib/proxy";

type Ctx = { params: Promise<{ id: string; msgId: string }> };

/**
 * Delete a turn (and the turns the backend decides followed it).
 *
 * The cascade lives on the server: only it knows how many rows came after the
 * target, and a client that guessed would leave an orphaned answer on screen if
 * it guessed low.
 */
export async function DELETE(_req: NextRequest, { params }: Ctx) {
  const { id, msgId } = await params;
  return proxyJson(`/conversations/${id}/messages/${msgId}`, {
    method: "DELETE",
  });
}

/**
 * Edit a user turn. The backend truncates everything after it and returns the
 * updated row, so the caller can replace it in place and drop the invalidated
 * descendants.
 */
export async function PATCH(req: NextRequest, { params }: Ctx) {
  const { id, msgId } = await params;
  const body = await req.text();
  return proxyJson(`/conversations/${id}/messages/${msgId}`, {
    method: "PATCH",
    body,
  });
}
