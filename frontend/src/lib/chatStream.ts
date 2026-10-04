/**
 * The one consumer of the /api/chat data stream.
 *
 * ## Why this is a module and not inline in the hook
 *
 * Two things now read the same stream: a live turn (`POST`) and a rejoin to a
 * run that is already in flight (`GET`). They carry the identical frames in the
 * identical dialect, and the browser cannot tell which one it received. A
 * second copy of the ~120-line frame loop would be two chances for them to
 * differ — and the failure mode is silent, not loud: a frame type the copy does
 * not handle is *dropped*, so the answer is quietly missing its citations, its
 * tool calls, or its verdict, with no error anywhere.
 *
 * This is the same hazard as an untranslated backend SSE frame, one layer up.
 * AGENTS.md §2 records that the backend emitting an event nobody translates is
 * indistinguishable from the backend not emitting it; the same is true here of
 * the browser side.
 *
 * So the frame loop lives here, once. Both entry points pass a `ChatStreamSink`
 * describing where to put what, and the two differ only in those callbacks.
 */
import type {
  ArtifactAnnotation,
  CitationAnnotation,
  HITLRequestAnnotation,
  NexusAnnotation,
  ToolCallAnnotation,
  TurnVerdict,
} from "@/hooks/useNexusChat";

/**
 * Where a consumer's side effects go.
 *
 * Every one is optional, because the two entry points genuinely want different
 * subsets: a live turn sets `hitlRequest` state, a rejoin has no pending
 * approval to hold and its one hitl frame arrives mid-answer. Making them
 * optional is honest about that rather than passing nulls a consumer has to
 * null-check anyway.
 */
export interface ChatStreamSink {
  /**
   * Called whenever the rendered answer or its annotations changed.
   *
   * Receives the *whole* rebuilt annotation list rather than a delta, because
   * the array is derived state: it is reconstructed from reasoning, citations
   * and tool calls on every patch, so a delta would have to be re-derived
   * anyway and would be wrong under replay.
   */
  patch: (content: string, annotations: NexusAnnotation[]) => void;
  /** A `3:` error frame, decoded to its message. */
  onError?: (message: string) => void;
  /**
   * The backend assigned a real conversation UUID to a newly-created thread.
   *
   * A callback rather than a returned field on purpose: the id arrives on the
   * last frame, and the page needs it while the turn is still finishing — a
   * caller reading a result field would get it only after the stream closed.
   */
  onConversationCreated?: (conversationId: string) => void;
  /** The turn was saved as a durable artifact. At most once per run. */
  onArtifactSaved?: (artifact: ArtifactAnnotation["data"]) => void;
  /** An approval is required before the run can continue. */
  onHitlRequest?: (data: HITLRequestAnnotation["data"]) => void;
  /**
   * The critic's verdict, and the evidence-quality score.
   *
   * Fires as each arrives rather than once at the end: they are independent
   * frames and `quality` can land without `critique`, so a consumer waiting for
   * both would show nothing when only one was sent.
   */
  onTurnVerdict?: (verdict: TurnVerdict) => void;
}

export interface ChatStreamResult {
  /** Everything the stream said, including what arrived before an error. */
  content: string;
  annotations: NexusAnnotation[];
  /** The decoded `3:` error, or null for a clean stream. */
  error: string | null;
}

/**
 * Read a data stream to completion, reporting progress through `sink`.
 *
 * Resolves rather than throws on a stream-level `3:` error: the point of that
 * frame is that the answer so far is still valid, and a consumer that caught an
 * exception would have to guess whether to keep the text. Network and
 * non-`AbortError` failures still reject — those carry no partial result.
 *
 * On an error the reader stops at the first error frame instead of draining the
 * rest of the stream. The backend closes immediately after emitting one, so
 * there is nothing behind it to collect, and continuing would risk applying
 * frames from a turn that has already been abandoned.
 */
export async function consumeChatStream(
  body: ReadableStream<Uint8Array>,
  sink: ChatStreamSink
): Promise<ChatStreamResult> {
  const annotations: NexusAnnotation[] = [];
  const citations: CitationAnnotation["data"][] = [];
  const toolCalls: ToolCallAnnotation["data"][] = [];
  let reasoningBuffer = "";
  // Accumulators, not state writes, because the two frames arrive independently
  // and `quality` can land without `critique`. Writing state per frame would
  // make the second one clobber the first.
  let verdictCritique: string | undefined;
  let verdictRevisions = 0;
  let verdictScore: number | undefined;
  let verdictGate: boolean | null = null;
  let streamContent = "";
  let streamError: string | null = null;

  const publishVerdict = () => {
    if (verdictCritique === undefined && verdictScore === undefined) return;
    sink.onTurnVerdict?.({
      critique: verdictCritique,
      revision_count: verdictRevisions,
      evidence_score: verdictScore ?? 0,
      evidence_gate_passed: verdictGate,
    });
  };

  // SSE lines can be split across network chunks (and JSON payloads may even
  // contain literal newlines), so any trailing partial line is carried into the
  // next iteration instead of being parsed eagerly.
  let buffer = "";

  const processLine = (line: string) => {
    if (!line) return;

    // Structured stream error (Vercel AI SDK 3: prefix)
    if (line.startsWith("3:")) {
      const raw = line.slice(2);
      try {
        streamError = JSON.parse(raw);
      } catch {
        streamError = raw;
      }
      return;
    }

    // Text delta
    if (line.startsWith("0:")) {
      const raw = line.slice(2);
      try {
        streamContent += JSON.parse(raw);
      } catch {
        streamContent += raw;
      }
      sink.patch(streamContent, annotations);
      return;
    }

    // Annotations
    if (!line.startsWith("8:")) return;
    try {
      const parsed: unknown[] = JSON.parse(line.slice(2));
      if (!Array.isArray(parsed)) return;
      for (const ann of parsed) {
        // Verdict frames are read structurally, before the `NexusAnnotation`
        // cast, because they are deliberately NOT part of that union: they are
        // not stored on the message. Casting them in would put a member in the
        // union that no code path ever pushes, which is a type that lies.
        const verdict = ann as { type?: string; data?: Record<string, unknown> };
        if (verdict.type === "critique") {
          const d = verdict.data ?? {};
          if (typeof d.critique === "string") verdictCritique = d.critique;
          if (typeof d.revision_count === "number") verdictRevisions = d.revision_count;
          publishVerdict();
          continue;
        }
        if (verdict.type === "quality") {
          const d = verdict.data ?? {};
          if (typeof d.evidence_score === "number") verdictScore = d.evidence_score;
          // `?? null` and not `?? true`: an unreported gate is not a pass, and
          // defaulting it to `true` turns the backend's silence into a clean bill
          // of health.
          verdictGate =
            typeof d.evidence_gate_passed === "boolean"
              ? d.evidence_gate_passed
              : null;
          publishVerdict();
          continue;
        }

        const typed = ann as NexusAnnotation;
        if (typed.type === "reasoning") {
          reasoningBuffer += typed.data.content ?? "";
        } else if (typed.type === "citation") {
          citations.push(typed.data);
        } else if (typed.type === "tool_call") {
          toolCalls.push({ ...typed.data, status: "running" });
        } else if (typed.type === "tool_result") {
          const call = toolCalls.find(
            (c) => c.tool_call_id === typed.data.tool_call_id
          );
          if (call) {
            call.status = "error" in typed.data ? "error" : "completed";
            call.result = typed.data.result;
          }
          annotations.push(typed);
        } else if (typed.type === "hitl_request") {
          sink.onHitlRequest?.(typed.data);
          annotations.push(typed);
        } else if (typed.type === "artifact") {
          // Not pushed into `annotations`: the array is rebuilt from
          // reasoning/citations/tool-calls on the next patch, so an entry here
          // would be dropped by the very next text delta. The callback is the
          // durable signal; the artifact itself lives in the database.
          sink.onArtifactSaved?.(typed.data);
        } else if (
          (ann as { type: string; data?: { thread_id?: string } }).type ===
          "conversation_created"
        ) {
          // Backend resolved a new conversation UUID — hand it straight on.
          // Fired here rather than collected: it arrives on the last frame, and
          // the page that adopts the new id needs it before the turn closes.
          const newId = (ann as { type: string; data: { thread_id: string } }).data
            ?.thread_id;
          if (newId) sink.onConversationCreated?.(newId);
        }
      }
      const next: NexusAnnotation[] = [
        ...annotations,
        ...(reasoningBuffer
          ? [
              {
                type: "reasoning" as const,
                data: { content: reasoningBuffer },
              },
            ]
          : []),
        ...citations.map(
          (c): CitationAnnotation => ({ type: "citation", data: c })
        ),
        ...toolCalls.map(
          (t): ToolCallAnnotation => ({
            type: "tool_call",
            data: t,
          })
        ),
      ];
      sink.patch(streamContent, next);
    } catch {
      // Ignore malformed annotation payloads
    }
  };

  const reader = body.getReader();
  const decoder = new TextDecoder();

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;

    buffer += decoder.decode(value, { stream: true });
    const lines = buffer.split("\n");
    buffer = lines.pop() ?? "";

    for (const line of lines) {
      processLine(line);
      if (streamError) break;
    }
    if (streamError) break;
  }

  // Flush a final line that arrived without a trailing newline.
  if (buffer && !streamError) processLine(buffer);

  const finalAnnotations: NexusAnnotation[] = [
    ...(reasoningBuffer
      ? [
          {
            type: "reasoning" as const,
            data: { content: reasoningBuffer },
          },
        ]
      : []),
    ...citations.map(
      (c): CitationAnnotation => ({ type: "citation", data: c })
    ),
    ...toolCalls.map(
      (t): ToolCallAnnotation => ({ type: "tool_call", data: t })
    ),
    ...annotations.filter((a) => a.type === "hitl_request"),
  ];
  // Even on a mid-stream error keep whatever already streamed so a failed
  // answer is never silently replaced by a blank bubble.
  sink.patch(streamContent, finalAnnotations);
  if (streamError) sink.onError?.(streamError);

  return {
    content: streamContent,
    annotations: finalAnnotations,
    error: streamError,
  };
}

/**
 * Header the BFF sets on both entry points, carrying the backend's run id.
 *
 * Read from the `POST` response so an interrupted answer can be re-attached to.
 * Absent when the run failed before it was ever created (auth rejection, a
 * validation error), which is why every reader of this treats it as optional
 * rather than assuming it.
 */
export const RUN_ID_HEADER = "X-Nexus-Run-Id";

/**
 * Header carrying the message row a finished run persisted. Set on the rejoin
 * (`GET`) response only, and only when the run got as far as writing its reply.
 *
 * The consumer uses it to decide whether the replay is needed at all: a run that
 * finished while the page was closed already has its answer in the conversation
 * history, and rendering the replay as well would show the user the same answer
 * twice. There is no way to infer this client-side — the frames carry no
 * message id — so it is stated by the only party that knows it.
 */
export const MESSAGE_ID_HEADER = "X-Nexus-Message-Id";

/** Read the run id off a chat response, or null when there is none. */
export function readRunId(response: Response): string | null {
  return response.headers.get(RUN_ID_HEADER);
}

/**
 * Read the persisted-message id off a chat response, or null when the run has
 * not produced one (still running, or it died before writing a row).
 */
export function readMessageId(response: Response): string | null {
  return response.headers.get(MESSAGE_ID_HEADER);
}