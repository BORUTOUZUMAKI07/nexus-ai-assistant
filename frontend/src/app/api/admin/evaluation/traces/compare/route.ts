import { NextRequest } from "next/server";
import { proxyJson } from "@/lib/proxy";

// Side-by-side trace comparison. Trace ids are user-supplied, so they are
// appended as properly encoded query parameters rather than interpolated into
// the path (which would let a `?` or `#` change the request).
export async function GET(req: NextRequest) {
  const traceA = req.nextUrl.searchParams.get("trace_a") ?? "";
  const traceB = req.nextUrl.searchParams.get("trace_b") ?? "";
  const query = new URLSearchParams({ trace_a: traceA, trace_b: traceB });
  return proxyJson(`/admin/evaluation/traces/compare?${query.toString()}`);
}
