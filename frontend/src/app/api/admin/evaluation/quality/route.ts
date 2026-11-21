import { proxyJson } from "@/lib/proxy";

// Deterministic response-quality metrics (tokens/sec, wording, formatting).
export async function POST(req: Request) {
  return proxyJson("/admin/evaluation/quality", {
    method: "POST",
    body: await req.text(),
  });
}
