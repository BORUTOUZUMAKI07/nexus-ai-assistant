import { NextRequest, NextResponse } from "next/server";
import { proxyJson } from "@/lib/proxy";

type Ctx = { params: Promise<{ id: string }> };

/**
 * Invite a colleague by email. The response carries an accept URL the owner
 * can hand over (built from the backend's public origin).
 */
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
