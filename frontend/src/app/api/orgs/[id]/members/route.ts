import { NextRequest } from "next/server";
import { proxyJson } from "@/lib/proxy";

type Ctx = { params: Promise<{ id: string }> };

/**
 * Members of an organization, visible to any of its members.
 *
 * Missing from the BFF entirely: the settings panel could create an
 * organization and invite people into it, but never show who was in it.
 */
export async function GET(_req: NextRequest, { params }: Ctx) {
  const { id } = await params;
  return proxyJson(`/orgs/${id}/members`);
}
