import { proxyJson } from "@/lib/proxy";

// Multi-turn compliance scoring across a full transcript.
export async function POST(req: Request) {
  return proxyJson("/admin/evaluation/conversational", {
    method: "POST",
    body: await req.text(),
  });
}
