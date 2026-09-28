import { NextRequest, NextResponse } from "next/server";
import { proxyJson } from "@/lib/proxy";

// POST /api/admin/optimization/run — start one prompt-optimization loop:
// propose K rewrites of a prompt key, score them against the golden case set,
// promote the winner if it beats the baseline. Synchronous and potentially
// slow, so it is not retried on failure.
export async function POST(req: NextRequest) {
  let body: unknown;
  try {
    body = await req.json();
  } catch {
    return NextResponse.json({ detail: "invalid_json_body" }, { status: 400 });
  }
  return proxyJson("/admin/optimization/run", {
    method: "POST",
    body: JSON.stringify(body),
  });
}
