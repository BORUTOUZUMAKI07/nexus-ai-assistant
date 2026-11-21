import { proxyJson } from "@/lib/proxy";

// RAGAS-style scores for one RAG turn. The backend scores the turn and writes
// the per-metric rows to evaluation_logs; this route only relays the payload.
export async function POST(req: Request) {
  return proxyJson("/admin/evaluation/rag", {
    method: "POST",
    body: await req.text(),
  });
}
