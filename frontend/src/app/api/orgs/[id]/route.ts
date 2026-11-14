import { NextRequest } from "next/server";
import { proxyJson } from "@/lib/proxy";

type Ctx = { params: Promise<{ id: string }> };

/**
 * Delete an organization the caller owns.
 *
 * The backend has always supported this; the BFF did not, which left an owner
 * with no way out — `leave` is rejected for owners ("transfer or delete the
 * organization instead"), so the organization and its memberships were
 * permanently stuck.
 */
export async function DELETE(_req: NextRequest, { params }: Ctx) {
  const { id } = await params;
  return proxyJson(`/orgs/${id}`, { method: "DELETE" });
}
