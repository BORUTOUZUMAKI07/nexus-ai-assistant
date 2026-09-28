import { proxyJson } from "@/lib/proxy";

// GET /api/admin/monitoring/fairness — fairness surface derived from the slice
// report: parity across models/providers rather than a single aggregate.
export async function GET() {
  return proxyJson("/admin/monitoring/fairness");
}
