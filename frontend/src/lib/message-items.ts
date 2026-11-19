/**
 * Stable derivation of the view models the transcript renders.
 *
 * `useNexusChat` already keeps untouched messages by object reference: a live
 * patch creates one new object for the streaming turn and leaves every other
 * turn alone. The page used to ignore that and rebuild a fresh `MessageItem`
 * (plus four annotation arrays) for *every* turn on every render. With a stream
 * that is once per painted frame, a long conversation paid O(all turns) work to
 * re-derive data that had not changed at all.
 *
 * Keying a WeakMap on the message object makes derivation incremental: the
 * patched turn is recomputed, its neighbours reuse their previous view model by
 * identity. That identity is also what lets memoized rows skip re-rendering.
 * The WeakMap holds no strong references, so removed turns are collected with
 * the message objects they describe.
 */
import type { CitationAnnotation, NexusMessage, ReasoningAnnotation, ToolCallAnnotation } from "@/hooks/useNexusChat"
import type { CitationItem, MessageItem, ToolCallItem } from "@/features/chat/ChatArea"

export interface MessageAnnotationGetters {
  getReasoningBlocks: (message: NexusMessage) => ReasoningAnnotation[]
  getCitations: (message: NexusMessage) => CitationAnnotation[]
  getToolCalls: (message: NexusMessage) => ToolCallAnnotation[]
}

export type MessageItemCache = WeakMap<NexusMessage, MessageItem>

export function createMessageItemCache(): MessageItemCache {
  return new WeakMap<NexusMessage, MessageItem>()
}

export function deriveMessageItems(
  messages: NexusMessage[],
  getters: MessageAnnotationGetters,
  cache: MessageItemCache
): MessageItem[] {
  return messages.map((message) => {
    const cached = cache.get(message)
    if (cached) return cached

    const thoughts = getters
      .getReasoningBlocks(message)
      .map((annotation) => annotation.data.content)
      .join("\n")
    const citations: CitationItem[] = getters.getCitations(message).map((annotation) => ({
      filename: annotation.data.filename ?? annotation.data.source ?? annotation.data.url ?? "source",
      chunk_index: (annotation.data as { chunk_index?: number }).chunk_index ?? 0,
      score: annotation.data.score ?? 0,
      content_snippet: annotation.data.content_snippet ?? annotation.data.snippet ?? "",
    }))
    const toolCalls: ToolCallItem[] = getters.getToolCalls(message).map((annotation) => ({
      name: annotation.data.tool_name,
      args: annotation.data.tool_input,
      result: annotation.data.result,
      status: annotation.data.status ?? "running",
    }))

    const item: MessageItem = {
      id: message.id,
      role: message.role,
      content: message.content,
      model: message.model,
      thought_process: thoughts || undefined,
      citations: citations.length > 0 ? citations : undefined,
      tool_calls: toolCalls.length > 0 ? toolCalls : undefined,
      created_at: typeof message.createdAt === "string" ? message.createdAt : undefined,
      imageDataUrl: message.imageDataUrl,
      // Preserve the durable row fields so a reload still shows the cost pill
      // and token chip on every old reply.
      cost_usd: message.cost_usd,
      total_tokens: message.total_tokens,
    }
    cache.set(message, item)
    return item
  })
}
