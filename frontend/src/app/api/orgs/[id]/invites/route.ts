import { NextRequest, NextResponse } from "next/server";
import { proxyJson } from "@/lib/proxy";

type Ctx = { params: Promise<{ id: string }> };

/**
 * Organization invites.
 *
 * GET lists the invites an owner/admin has already sent (tokens are never
 * repeated — the accept link is handed out once, at creation). POST creates a
 * new one.
 */
export async function GET(_req: NextRequest, { params }: Ctx) {
  const { id } = await params;
  return proxyJson(`/orgs/${id}/invites`);
}

export async function POST(req: NextRequest, { params }: Ctx) {
  const { id } = await params;
  let body: unknown;
  try {
    body = await req.json();
  } catch {
    return NextResponse.json({ detail: "invalid_json_body" }, { status: 400 });
  }
  return proxyJson(`/orgs/${id}/invites`, {
    method: "POST",
    body: JSON.stringify(body),
  });
}
