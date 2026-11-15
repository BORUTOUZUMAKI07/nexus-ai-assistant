import { NextRequest, NextResponse } from "next/server";
import { proxyJson } from "@/lib/proxy";

/**
 * Prompt library templates.
 *
 * `backend/app/api/v1/prompts.py` has served these since the prompt service
 * landed, but no BFF route existed, so the deployed app could not list a single
 * template — the feature was described in the docs and unreachable in the
 * product.
 */
export async function GET(req: NextRequest) {
  const includePublic = req.nextUrl?.searchParams.get("include_public") ?? "true";
  return proxyJson(`/prompts/templates?include_public=${encodeURIComponent(includePublic)}`);
}

export async function POST(req: NextRequest) {
  let body: unknown;
  try {
    body = await req.json();
  } catch {
    return NextResponse.json({ detail: "invalid_json_body" }, { status: 400 });
  }
  return proxyJson("/prompts/templates", {
    method: "POST",
    body: JSON.stringify(body),
  });
}
