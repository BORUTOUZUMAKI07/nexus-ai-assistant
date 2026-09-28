import { proxyJson } from "@/lib/proxy";

// GET /api/admin/monitoring/bandits — current epsilon-greedy bandit selection
// state: which variant each arm is serving and its recent reward.
export async function GET() {
  return proxyJson("/admin/monitoring/bandits");
}
