import { NextRequest, NextResponse } from "next/server";
import { proxyJson } from "@/lib/proxy";

/** Redeem an invite token and join its organization. */
export async function POST(req: NextRequest) {
  let body: unknown;
  try {
    body = await req.json();
  } catch {
    return NextResponse.json({ detail: "invalid_json_body" }, { status: 400 });
  }
  return proxyJson("/orgs/invites/accept", {
    method: "POST",
    body: JSON.stringify(body),
  });
}
