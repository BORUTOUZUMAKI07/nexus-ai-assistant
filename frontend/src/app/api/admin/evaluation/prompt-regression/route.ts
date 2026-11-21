import { proxyJson } from "@/lib/proxy";

// CI prompt-regression gate against the guardrail golden set.
export async function POST(req: Request) {
  return proxyJson("/admin/evaluation/prompt-regression", {
    method: "POST",
    body: await req.text(),
  });
}
