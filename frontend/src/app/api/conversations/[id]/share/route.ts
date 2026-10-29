import { NextRequest } from "next/server";
import { proxyJson } from "@/lib/proxy";

type Ctx = { params: Promise<{ id: string }> };

/** Share links already minted for this conversation. */
export async function GET(_req: NextRequest, { params }: Ctx) {
  const { id } = await params;
  return proxyJson(`/shares/${id}`);
}

/**
 * Mint a revocable read-only link. The backend's create body is optional but
 * required by the schema, so an empty object is sent explicitly.
 */
export async function POST(_req: NextRequest, { params }: Ctx) {
  const { id } = await params;
  return proxyJson(`/shares/${id}`, {
    method: "POST",
    body: JSON.stringify({}),
  });
}

/** Revoke every link for this conversation. */
export async function DELETE(_req: NextRequest, { params }: Ctx) {
  const { id } = await params;
  return proxyJson(`/shares/${id}`, { method: "DELETE" });
}