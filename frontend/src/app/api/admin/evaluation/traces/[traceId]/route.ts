import { proxyJson } from "@/lib/proxy";

// Full span for a single trace (events, cost, tokens, model, confidence).
export async function GET(
  _req: Request,
  ctx: { params: Promise<{ traceId: string }> },
) {
  const { traceId } = await ctx.params;
  return proxyJson(`/admin/evaluation/traces/${encodeURIComponent(traceId)}`);
}
