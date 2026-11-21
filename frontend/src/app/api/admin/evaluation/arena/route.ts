import { proxyJson } from "@/lib/proxy";

// Pairwise "arena as judge" comparison of two outputs for the same query.
export async function POST(req: Request) {
  return proxyJson("/admin/evaluation/arena", {
    method: "POST",
    body: await req.text(),
  });
}
