import { proxyJson } from "@/lib/proxy";

// GET /api/admin/optimization/runs — the prompt-optimization evidence trail:
// one row per closed loop, with candidate scores and the accepted variant.
export async function GET() {
  return proxyJson("/admin/optimization/runs");
}
