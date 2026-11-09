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
 * project guidelines records that the backend emitting an event nobody translates is
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
/**
 * Coalesce `patch` calls to at most one per animation frame.
 *
 * Every text frame carries the *whole* answer rebuilt so far, so intermediate
 * patches are pure waste: the backend emits tens of frames a second, and each
 * one used to write the transcript and re-render the entire workspace — the
 * transcript list, the sidebar, the composer, and a Markdown parse of the growing
 * answer — before the next frame arrived. Keeping only the latest patch and
 * applying it in the next frame turns N renders per token burst into at most one
 * per painted frame, and never loses text, because the patch is a snapshot
 * rather than a delta.
 *
 * `flush()` is not optional for a caller: a stream that ends mid-frame must
 * still show its last text, so callers run it when `consumeChatStream` settles
 * (including on abort or error) rather than relying on a frame callback that may
 * never fire while the tab is in the background.
 */
export interface CoalesceOptions {
  /** When true, skip RAF batching and apply each patch immediately. */
  immediate?: boolean;
}

export function coalescePatches(
  apply: ChatStreamSink["patch"],
  options: CoalesceOptions = {}
): {
  patch: ChatStreamSink["patch"];
  flush: () => void;
} {
  let pending: { content: string; annotations: NexusAnnotation[] } | null = null;
  let frame: number | null = null;
  const immediate = options.immediate ?? false;

  const flush = () => {
    if (frame !== null) {
      cancelAnimationFrame(frame);
      frame = null;
    }
    if (!pending) return;
    const next = pending;
    pending = null;
    apply(next.content, next.annotations);
  };

  const patch: ChatStreamSink["patch"] = (content, annotations) => {
    // Text deltas and other immediate patches bypass RAF batching so tokens
    // paint as they arrive instead of waiting for the next animation frame.
    if (immediate) {
      apply(content, annotations);
      return;
    }
    pending = { content, annotations };
    if (frame !== null) return;
    // No `requestAnimationFrame` outside a browser (a test runner without a
    // DOM, a server render): apply immediately rather than dropping the text.
    if (typeof requestAnimationFrame !== "function") {
      flush();
      return;
    }
    frame = requestAnimationFrame(() => {
      frame = null;
      flush();
    });
  };

  return { patch, flush };
}

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
   * The model that actually answered the turn (provider model string).
   *
   * Reported post-run from the backend's checkpoint, so it reflects a provider
   * fallback rather than the group the picker requested. Fires at most once per
   * run and may not fire at all when the backend could not resolve a model.
   */
  onModelResolved?: (model: string) => void;
  /**
   * The critic's verdict, and the evidence-quality score.
   *
   * Fires as each arrives rather than once at the end: they are independent
   * frames and `quality` can land without `critique`, so a consumer waiting for
   * both would show nothing when only one was sent.
   */
  onTurnVerdict?: (verdict: TurnVerdict) => void;
  /**
   * The stage the backend is currently working on, as a human label.
   *
   * The synthesizer streams only its finished, guardrailed text, so an
   * agent-mode turn emits nothing for a long time before its first token. These
   * frames are the progress signal in that window (`status` frames from
   * services/run_executor.py). A consumer that ignores this is not broken — it
   * just has no progress to show.
   */
  onStatus?: (label: string) => void;
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

    // Backend-native JSON frame (forwarded raw by the BFF, no AI SDK
    // re-encoding in between). Parsing once here instead of
    // parse→stringify→parse in the BFF eliminates double JSON work per frame.
    let parsed: Record<string, unknown>;
    try {
      parsed = JSON.parse(line);
    } catch {
      return;
    }

    const eventType = parsed.type;

    // Structured stream error
    if (eventType === "error") {
      const msg = parsed.message ?? parsed.error ?? "An error occurred";
      streamError = typeof msg === "string" ? msg : String(msg);
      return;
    }

    // Text delta
    if (eventType === "text_delta") {
      const content =
        typeof parsed.content === "string" ? parsed.content : "";
      streamContent += content;
      sink.patch(streamContent, annotations);
      return;
    }

    // Annotation frames: tool_call, tool_result, thinking, citation,
    // hitl_request, critique, quality, status, model_info, artifact,
    // conversation_created
    if (
      !(
        eventType === "tool_call" ||
        eventType === "tool_result" ||
        eventType === "thinking" ||
        eventType === "citation" ||
        eventType === "hitl_request" ||
        eventType === "critique" ||
        eventType === "quality" ||
        eventType === "status" ||
        eventType === "model_info" ||
        eventType === "artifact" ||
        eventType === "conversation_created"
      )
    ) {
      return;
    }

    const data = parsed.data ?? {};
    // Verdict frames are read structurally, before the `NexusAnnotation`
    // cast, because they are deliberately NOT part of that union: they are
    // not stored on the message. Casting them in would put a member in the
    // union that no code path ever pushes, which is a type that lies.
    const verdict = data as Record<string, unknown>;
    if (eventType === "critique") {
      if (typeof verdict.critique === "string") verdictCritique = verdict.critique;
      if (typeof verdict.revision_count === "number") verdictRevisions = verdict.revision_count;
      publishVerdict();
      return;
    }
    if (eventType === "quality") {
      if (typeof verdict.evidence_score === "number") verdictScore = verdict.evidence_score;
      verdictGate =
        typeof verdict.evidence_gate_passed === "boolean"
          ? verdict.evidence_gate_passed
          : null;
      publishVerdict();
      return;
    }
    if (eventType === "status") {
      const d = data as { label?: string };
      if (typeof d.label === "string" && d.label) sink.onStatus?.(d.label);
      return;
    }
    if (eventType === "model_info") {
      const d = data as { model?: string };
      if (typeof d.model === "string") sink.onModelResolved?.(d.model);
      return;
    }
    if (eventType === "conversation_created") {
      const d = data as { thread_id?: string };
      const newId = d.thread_id;
      if (typeof newId === "string") sink.onConversationCreated?.(newId);
      return;
    }

    const typed = data as NexusAnnotation;
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
      (typed as { type: string; data?: { thread_id?: string } }).type ===
      "conversation_created"
    ) {
      const newId = (
        typed as { type: string; data: { thread_id: string } }
      ).data?.thread_id;
      if (newId) sink.onConversationCreated?.(newId);
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