import { proxyJson } from "@/lib/proxy";

// GET /api/admin/audit/redteam — persisted red-team probe runs (defense rate
// and per-probe verdicts over time), so block-rate trends survive a restart.
// The backend answers with a { runs: [...] } envelope.
export async function GET() {
  return proxyJson("/admin/audit/redteam");
}
