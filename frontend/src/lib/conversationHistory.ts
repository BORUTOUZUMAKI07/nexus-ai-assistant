/**
 * Turning a conversation's stored messages into what the chat view renders.
 *
 * ## Why this exists
 *
 * The chat view held no history at all: `messages` started empty and was only
 * ever appended to by a live stream or a rejoin. So selecting a conversation you
 * had already had a dozen turns in showed a blank panel, and the rejoin feature
 * landed a recovered answer with no user turn anywhere near it. §9.27 of
 * AGENTS.md recorded that as a pre-existing gap and declined to invent a user
 * bubble — correctly, because a bubble the backend never wrote is fabricated
 * conversation. The fix is not to invent one; it is to read the real rows.
 *
 * ## Why the annotations are rebuilt here and not left to the stream
 *
 * Citations and tool calls are columns on the message row, so a reloaded
 * transcript can show them. Without this they were silently dropped on refresh:
 * the answer text came back and its evidence did not, which reads as the
 * citation panel being broken rather than as history not being loaded.
 *
 * What genuinely cannot be recovered is marked rather than faked — see
 * `thought_process` below and the module's own test for it.
 */
import type {
  CitationAnnotation,
  NexusAnnotation,
  NexusMessage,
  ReasoningAnnotation,
  ToolCallAnnotation,
} from "@/hooks/useNexusChat";
import type { ConversationMessage } from "@/lib/api";

/**
 * A history row the view must not render.
 *
 * The backend writes a row for the user turn and one for the assistant reply,
 * and the empty-string content check is the only shape rule here: a blank
 * assistant bubble is what an interrupted or content-filtered turn leaves behind,
 * and rendering it puts an empty box in the transcript where the user expects
 * their answer. Rows whose *whole* record is absent never reach here.
 */
function isRenderable(row: ConversationMessage): boolean {
  return typeof row.content === "string" && row.content.length > 0;
}

/**
 * Citations, tolerating both spellings the backend has used.
 *
 * `filename`/`chunk_index` and `source`/`snippet`/`url` are two shapes the same
 * list has carried across revisions of the RAG code, and a stored row is
 * whatever was current when it was written. Reading only one pair renders some
 * historical messages with no citation text and no error.
 */
function citationsFrom(row: ConversationMessage): CitationAnnotation[] {
  const rows = Array.isArray(row.citations) ? row.citations : [];
  return rows
    .filter((c): c is NonNullable<typeof c> => Boolean(c))
    .map((c) => ({
      type: "citation" as const,
      data: {
        // `source` is the citation's own label and `filename` its document; the
        // previous mapper set `source` from `filename`, so a RAG citation (which
        // only has a filename) and a web citation (which only has a source)
        // both rendered under the wrong label and one of them rendered blank.
        source: c.source ?? c.filename,
        snippet: c.snippet ?? c.content_snippet,
        score: c.score,
        filename: c.filename,
        content_snippet: c.content_snippet,
      },
    }));
}

/**
 * Tool calls, tolerating `tool_name`/`name` and `tool_input`/`args` the same way.
 *
 * A tool call with no name is dropped rather than rendered as an empty card: the
 * card's whole content is its name, so an unnamed one is a rendering bug in the
 * producer, and showing it would put an empty box in the transcript.
 */
function toolCallsFrom(row: ConversationMessage): ToolCallAnnotation[] {
  const calls = Array.isArray(row.tool_calls) ? row.tool_calls : [];
  const out: ToolCallAnnotation[] = [];
  for (const call of calls) {
    if (!call) continue;
    // `tool_name`/`tool_input` are what the run executor writes and what the live
    // stream carries; `name`/`args` are the older spelling. The previous mapper
    // read only the old pair, so every tool call stored by the streaming path
    // rendered with no name at all -- an empty card in the transcript.
    const name = call.tool_name ?? call.name;
    if (typeof name !== "string" || name.length === 0) continue;
    const input = call.tool_input ?? call.args;
    out.push({
      type: "tool_call",
      data: {
        tool_name: name,
        tool_input:
          input && typeof input === "object" && !Array.isArray(input)
            ? (input as Record<string, unknown>)
            : {},
        tool_call_id: call?.tool_call_id ?? `${row.id}-${name}`,
        status: call?.status === "error" ? "error" : "completed",
        result: call?.result,
      },
    });
  }
  return out;
}

/**
 * The model's reasoning, when the row kept it.
 *
 * Present only on rows written by a version that persisted `thought_process`, so
 * most messages have none. It is not reconstructed from anywhere else and not
 * approximated: a message without a stored trace simply shows no reasoning
 * block, which is honest, rather than one that implies the model never reasoned.
 */
function reasoningFrom(row: ConversationMessage): ReasoningAnnotation[] {
  const text = row.thought_process;
  if (typeof text !== "string" || text.length === 0) return [];
  return [{ type: "reasoning", data: { content: text } }];
}

/** Map one stored row to one rendered message. */
function toMessage(row: ConversationMessage): NexusMessage {
  const annotations: NexusAnnotation[] = [
    ...reasoningFrom(row),
    ...toolCallsFrom(row),
    ...citationsFrom(row),
  ];
  return {
    id: row.id,
    role: row.role,
    content: row.content,
    model: row.model ?? undefined,
    createdAt: row.created_at,
    // Attached only when there is something in it. `annotations: []` on every
    // message is not equivalent in the components: several of them treat an
    // absent list and an empty one differently, and history messages have no
    // annotations far more often than they have some.
    ...(annotations.length > 0 ? { annotations } : {}),
  };
}

/**
 * A conversation's stored rows, in the order the view renders them.
 *
 * The order is the backend's, which is chronological, and it is deliberately not
 * re-sorted here: the backend already had to choose a window (the most recent
 * `limit` messages) and sorting again client-side would only be able to
 * re-implement that choice wrongly.
 */
export function messagesFromHistory(
  rows: ConversationMessage[] | null | undefined
): NexusMessage[] {
  if (!Array.isArray(rows)) return [];
  return rows.filter(isRenderable).map(toMessage);
}

/**
 * Whether a run's answer is already rendered, given the message id the backend
 * persisted it under.
 *
 * This is the duplicate-answer guard for rejoin: the replay starts at frame 0,
 * so a run that finished while the page was closed would otherwise be shown both
 * as a row in the loaded history and as a freshly streamed bubble. Identity
 * decides it — not row count, not text equality, both of which are wrong the
 * moment two runs answer alike or one produces nothing.
 */
export function hasAnswer(
  messages: NexusMessage[],
  persistedMessageId: string | null | undefined
): boolean {
  if (!persistedMessageId) return false;
  return messages.some((m) => m.id === persistedMessageId);
}