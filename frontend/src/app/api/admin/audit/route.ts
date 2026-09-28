import { proxyJson } from "@/lib/proxy";

// GET /api/admin/audit — the responsible-ML / compliance read model that
// aggregates the evidence pack (EU AI Act classification, retention
// statements, transparency obligations, fine exposure).
//
// Distinct from /api/admin/audit-logs, which is the raw row-level trail.
export async function GET() {
  return proxyJson("/admin/audit");
}
