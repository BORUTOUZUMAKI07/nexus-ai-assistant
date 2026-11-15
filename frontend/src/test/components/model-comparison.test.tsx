import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, waitFor } from "@/test/test-utils";
import { ModelComparison } from "@/features/chat/ModelComparison";
import { MODELS } from "@/lib/models";

/**
 * Comparison must ask two *different* models.
 *
 * The page passed the literals "gpt-4o-mini" and "gpt-4o". Neither is a router
 * group the backend whitelists, so `resolve_answer_model` answered both panes
 * with the same default — a comparison of one model against itself, drawn under
 * two different labels. This test pins the request bodies to the catalog ids, so
 * a placeholder string cannot come back.
 */

type ChatBody = {
  messages: { role: string; content: string }[];
  conversationId: string | null;
  model?: string;
};

function streamOf(text: string): ReadableStream<Uint8Array> {
  const encoder = new TextEncoder();
  return new ReadableStream({
    start(controller) {
      controller.enqueue(encoder.encode(`0:${JSON.stringify(text)}\n`));
      controller.enqueue(encoder.encode("data: [DONE]\n"));
      controller.close();
    },
  });
}

describe("ModelComparison", () => {
  const chatBodies: ChatBody[] = [];
  const originalFetch = globalThis.fetch;

  beforeEach(() => {
    chatBodies.length = 0;
    globalThis.fetch = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = typeof input === "string" ? input : input.toString();
      if (url.startsWith("/api/conversations") && init?.method === "POST") {
        return new Response(JSON.stringify({ id: "conv-compare" }), { status: 201 });
      }
      if (url.startsWith("/api/conversations/") && init?.method === "PATCH") {
        return new Response(JSON.stringify({ ok: true }), { status: 200 });
      }
      if (url.startsWith("/api/chat")) {
        chatBodies.push(JSON.parse(String(init?.body)) as ChatBody);
        return new Response(streamOf("answer"), {
          status: 200,
          headers: { "Content-Type": "text/plain" },
        });
      }
      return new Response("{}", { status: 200 });
    }) as unknown as typeof fetch;
  });

  afterEach(() => {
    globalThis.fetch = originalFetch;
    window.localStorage.clear();
  });

  it("asks each pane with a distinct, selectable router group", async () => {
    render(
      <ModelComparison
        onClose={() => {}}
        question="What is a vector database?"
        modelA={MODELS[1].id}
        modelB={MODELS[0].id}
      />
    );

    await waitFor(() => expect(chatBodies.length).toBe(2));

    const models = chatBodies.map((body) => body.model);
    expect(models).toEqual([MODELS[1].id, MODELS[0].id]);
    expect(new Set(models).size).toBe(2);
    // Both panes are the same question in the same hidden conversation, so the
    // transcript only ever gains one conversation row.
    expect(chatBodies[0].conversationId).toBe("conv-compare");
    expect(chatBodies[1].conversationId).toBe("conv-compare");

    await waitFor(() => expect(screen.getAllByText("answer")).toHaveLength(2));
  });

  it("creates and archives the shared comparison conversation once", async () => {
    const { unmount } = render(
      <ModelComparison onClose={() => {}} question="Explain RAG" modelA={MODELS[1].id} modelB={MODELS[0].id} />
    );
    await waitFor(() => expect(chatBodies.length).toBe(2));
    expect(window.localStorage.getItem("nexus.compareConversationId")).toBe("conv-compare");
    unmount();

    // A second comparison reuses it instead of creating another conversation.
    const calls = (globalThis.fetch as unknown as { mock: { calls: unknown[][] } }).mock.calls.length;
    render(
      <ModelComparison onClose={() => {}} question="Explain RAG again" modelA={MODELS[1].id} modelB={MODELS[0].id} />
    );
    await waitFor(() => expect(chatBodies.length).toBe(4));
    expect((globalThis.fetch as unknown as { mock: { calls: unknown[][] } }).mock.calls.length).toBe(calls + 2);
  });
});
