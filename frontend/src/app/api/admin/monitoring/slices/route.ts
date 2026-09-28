import { proxyJson } from "@/lib/proxy";

// GET /api/admin/monitoring/slices — popularity-bucketed per-(model, provider)
// slice report: volume, error rate and latency for each slice.
export async function GET() {
  return proxyJson("/admin/monitoring/slices");
}
