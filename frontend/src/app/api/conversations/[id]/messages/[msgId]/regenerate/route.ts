import { proxyJson } from "@/lib/proxy";

type Ctx = { params: Promise<{ id: string; msgId: string }> };

/**
 * Drop an assistant reply and return the prompt that produced it.
 *
 * The response body carries `prompt`, which the client re-runs through the chat
 * stream. Deleting first and resending second is what keeps the transcript
 * honest if the resend fails: the stale answer is already gone.
 */
export async function POST(_req: Request, { params }: Ctx) {
  const { id, msgId } = await params;
  return proxyJson(`/conversations/${id}/messages/${msgId}/regenerate`, {
    method: "POST",
  });
}
