import { NextRequest, NextResponse } from "next/server";
import { proxyJson } from "@/lib/proxy";

type Ctx = { params: Promise<{ id: string }> };

/**
 * Organization detail.
 *
 * The backend has served GET and PATCH since the multi-tenant layer landed, but
 * this BFF only forwarded DELETE — so the deployed app could not read an
 * organization's member count and could not rename one at all (the settings
 * panel had no edit affordance to wire up, and a live probe answered 405).
 */
export async function GET(_req: NextRequest, { params }: Ctx) {
  const { id } = await params;
  return proxyJson(`/orgs/${id}`);
}

/** Rename / re-describe an organization the caller owns or administers. */
export async function PATCH(req: NextRequest, { params }: Ctx) {
  const { id } = await params;
  let body: unknown;
  try {
    body = await req.json();
  } catch {
    return NextResponse.json({ detail: "invalid_json_body" }, { status: 400 });
  }
  return proxyJson(`/orgs/${id}`, {
    method: "PATCH",
    body: JSON.stringify(body),
  });
}

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
