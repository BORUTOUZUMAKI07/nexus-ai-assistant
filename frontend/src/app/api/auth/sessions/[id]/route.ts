import { NextRequest } from "next/server";
import { proxyJson } from "@/lib/proxy";

type Ctx = { params: Promise<{ id: string }> };

/**
 * Revoke one sign-in by its refresh-token family id. Next passes dynamic
 * params as a Promise; the backend scopes the id to the current user.
 */
export async function DELETE(_req: NextRequest, { params }: Ctx) {
  const { id } = await params;
  return proxyJson(`/auth/sessions/${id}`, { method: "DELETE" });
}
