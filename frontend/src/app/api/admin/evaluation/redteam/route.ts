import { proxyJson } from "@/lib/proxy";

// The adversarial probe battery is defined by the backend, so it takes no body
// from the browser. Each run is persisted into redteam_runs upstream.
export async function POST() {
  return proxyJson("/admin/evaluation/redteam", { method: "POST" });
}
