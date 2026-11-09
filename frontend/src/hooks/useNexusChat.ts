/**
 * Nexus AI – useNexusChat hook
 * Typed streaming chat state for Nexus conversations:
 *  - streams assistant replies from the /api/chat data-stream proxy
 *  - surfaces tool calls, citations, and reasoning as typed annotations
 *  - manages human-in-the-loop (HITL) approval requests
 */
"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import {
  fetchWithSessionRecovery,
  fetchConversation,
  sendHITLFeedback,
  forkConversation,
  deleteMessage as apiDeleteMessage,
  editMessage as apiEditMessage,
  regenerateMessage as apiRegenerateMessage,
  type ConversationMode,
} from "@/lib/api";
import {
  coalescePatches,
  consumeChatStream,
  readMessageId,
  readRunId,
} from "@/lib/chatStream";
import { hasAnswer, messagesFromHistory } from "@/lib/conversationHistory";

export interface ToolCallAnnotation {
  type: "tool_call";
  data: {
    tool_name: string;
    tool_input: Record<string, unknown>;
    tool_call_id: string;
    status?: "running" | "completed" | "error";
    result?: unknown;
  };
}

export interface ToolResultAnnotation {
  type: "tool_result";
  data: {
    tool_call_id: string;
    result: unknown;
    error?: string;
  };
}

export interface ReasoningAnnotation {
  type: "reasoning";
  data: { content: string };
}

export interface CitationAnnotation {
  type: "citation";
  data: {
    source?: string;
    url?: string;
    snippet?: string;
    score?: number;
    filename?: string;
    content_snippet?: string;
  };
}

export interface HITLRequestAnnotation {
  type: "hitl_request";
  data: {
    thread_id: string;
    request: string;
    plan?: string[];
    tool_name?: string;
    arguments?: Record<string, unknown>;
    state?: { next: string[]; ts?: string };
  };
}

export interface ArtifactAnnotation {
  type: "artifact";
  data: {
    artifact_id: string;
    title: string;
    version: number;
    /** false when an existing artifact was re-versioned rather than created. */
    created: boolean;
  };
}

/**
 * The critic's verdict on a finished answer, and the evidence-quality score.
 *
 * Field names mirror `services/run_events.py::finished_run_events` exactly.
 * They are NOT placed in `NexusMessage.annotations`: like `artifact`, they
 * arrive after the last text delta, and the annotation array is rebuilt on
 * every patch, so an entry there is a race between two arrival orders rather
 * than a contract. They get their own state instead.
 */
export interface TurnVerdict {
  /** Present only when the run actually went through the critic's loop. */
  critique?: string;
  revision_count: number;
  evidence_score: number;
  /** null when the backend did not report it; false is a real admission. */
  evidence_gate_passed: boolean | null;
}

export type NexusAnnotation =
  | ToolCallAnnotation
  | ToolResultAnnotation
  | ReasoningAnnotation
  | CitationAnnotation
  | HITLRequestAnnotation
  | ArtifactAnnotation;

export interface NexusMessage {
  id: string;
  role: "user" | "assistant" | "system";
  content: string;
  model?: string;
  annotations?: NexusAnnotation[];
  createdAt?: string;
  /** base64 data URL of an attached image (user messages only) */
  imageDataUrl?: string;
  /** Durable cost of this turn, persisted server-side. Zero for user messages. */
  cost_usd?: number;
  /** Total tokens for this turn. */
  total_tokens?: number;
}

export interface UseNexusChatOptions {
  conversationId?: string;
  userId?: string;
  mode?: ConversationMode;
  model?: string;
  /** Called once when the backend assigns a real UUID to a newly-created conversation. */
  onConversationCreated?: (newConversationId: string) => void;
  /**
   * Called when a turn is saved as a durable artifact, so the page can open the
   * canvas without polling. Fires at most once per turn, and not at all on the
   * overwhelmingly common turn that was just a chat reply.
   *
   * A callback rather than message state on purpose: the annotation array is
   * rebuilt from reasoning/citations/tool-calls on every patch, so an artifact
   * entry placed there would be dropped by the next text delta. The artifact
   * itself is durable server-side, so this is a live signal only -- reloading
   * the page re-fetches it from the artifacts API.
   */
  onArtifactSaved?: (artifact: ArtifactAnnotation["data"]) => void;
}

export interface SendMessageOptions {
  mode?: ConversationMode;
  conversationId?: string;
  /** base64 data URL for an image the user has attached */
  imageDataUrl?: string;
  /**
   * Approved-plan preamble: when an approved plan is executed, the backend
   * prepends this text to the agent system prompt so the run follows the
   * plan's approved steps (Plan mode execution reuses the agent stream).
   */
  planPreamble?: string;
  /**
   * The stored user row a resend is re-asking, so the server adopts it instead
   * of appending a second copy of the question. Set by edit-and-resend (the
   * edited row's id) and by regenerate (the id the endpoint returns).
   */
  reuseUserMessageId?: string;
}

/**
 * A user turn accepted while another run was in flight.
 *
 * The backend stream is strictly sequential -- one turn at a time -- so a send
 * during a run cannot be dispatched immediately. It is held here and drained
 * when the active run releases the slot. The queue exists so the second
 * keystroke is *captured* rather than silently discarded, which is what the
 * previous `if (loadingRef.current) return` did.
 */
export interface QueuedMessage {
  id: string;
  content: string;
  options?: SendMessageOptions;
}

// ── Rejoin: remembering which run a conversation was streaming ──────────────
//
// The backend no longer runs a turn inside the request that asked for it, so an
// answer keeps being produced whether or not the browser is still listening.
// That is what makes it recoverable, and it costs one localStorage entry per
// conversation to make it so.
//
// Only the run *id* is stored, never the frames: the durable copy lives on the
// server, and a browser that cached the answer would be a second source of
// truth for it, stale the moment the backend appends another frame.

const RUN_STORAGE_PREFIX = "nexus-active-run:";

/** A run id is a UUID; anything else in storage is not one of ours. */
const UUID_RE =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

function runStorageKey(conversationId: string) {
  return `${RUN_STORAGE_PREFIX}${conversationId}`;
}

function rememberRun(conversationId: string, runId: string) {
  if (!conversationId) return;
  try {
    window.localStorage.setItem(runStorageKey(conversationId), runId);
  } catch {
    // Storage can be unavailable (private mode, quota, disabled cookies). The
    // run still completes and is still persisted server-side; the only thing
    // lost is the ability to re-attach to it after a reload, which is a
    // degradation rather than a failure. Nothing here is worth an error banner.
  }
}

/**
 * Drop a stored run id.
 *
 * Accepts null, and null is meaningful rather than a defensive branch: "we never
 * filed this turn's run under any key", which happens when the response carried
 * no run id (the turn failed before a run existed) or when the client never
 * named the conversation and the backend never resolved one. Widening the type
 * here keeps that from having to be narrowed at the call site with a cast or an
 * `if` whose only purpose is to satisfy the compiler.
 */
function forgetRun(conversationId: string | null) {
  if (!conversationId) return;
  try {
    window.localStorage.removeItem(runStorageKey(conversationId));
  } catch {
    // Same reasoning as rememberRun: the entry expires with the session anyway.
  }
}

/**
 * An error that carries the backend's own explanation.
 *
 * `statusText` is the HTTP reason phrase — "Conflict", "Bad Request" — which
 * told the user nothing about what to do. The API puts a sentence in `detail`
 * ("This conversation is already streaming. Please wait for it to finish."), so
 * read it when it is there and fall back to the status line otherwise.
 */
async function chatErrorFromResponse(response: Response): Promise<Error> {
  let detail = response.statusText;
  try {
    const body: unknown = await response.json();
    if (
      body &&
      typeof body === "object" &&
      "detail" in body &&
      typeof (body as { detail: unknown }).detail === "string"
    ) {
      detail = (body as { detail: string }).detail;
    }
  } catch {
    // Not JSON, or already consumed: the status line is still honest.
  }
  return new Error(
    detail ? `Chat API error ${response.status}: ${detail}` : `Chat API error: ${response.statusText}`
  );
}

function readRun(conversationId: string): string | null {
  if (!conversationId) return null;
  try {
    const raw = window.localStorage.getItem(runStorageKey(conversationId));
    return raw && UUID_RE.test(raw) ? raw : null;
  } catch {
    return null;
  }
}

/**
 * Whether an id names a persisted backend row.
 *
 * Live turns carry client-generated ids (`msg-<timestamp>`); anything loaded
 * from history or recovered by a rejoin carries the server's UUID. Only the
 * second kind can be addressed by the message endpoints, and the two are
 * otherwise indistinguishable at the call site, so the shape is tested here
 * rather than guessed from position in the list — a live turn sitting above a
 * hydrated one is the normal state after an edit-and-resend.
 *
 * Module scope because it is pure: delete, edit, regenerate and id adoption
 * all ask the same question, and a helper declared inside any one of them
 * would be a forward reference (or a dependency) for all the others.
 */
function isServerId(id: string): boolean {
  return UUID_RE.test(id);
}

export function useNexusChat(options: UseNexusChatOptions = {}) {
  const [messages, setMessages] = useState<NexusMessage[]>([]);
  const [input, setInput] = useState("");
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<Error | null>(null);
  const [pendingHITL, setPendingHITL] = useState<
    HITLRequestAnnotation["data"] | null
  >(null);
  // Critic verdict + evidence score for the run currently streaming. Scoped to
  // the stream rather than to a message id because the backend emits these
  // AFTER the assistant message row is written, so there is no id to key on --
  // the same constraint artifact identity hits in artifact_node.py.
  const [turnVerdict, setTurnVerdict] = useState<TurnVerdict | null>(null);
  // The stage the backend says it is on ("Checking evidence", "Writing the
  // answer"). Cleared with the verdict at the start of every turn, because a
  // label from the previous run would describe work that is not happening.
  const [statusLabel, setStatusLabel] = useState<string | null>(null);
  // User turns submitted while a run was in flight. The ref is the synchronous
  // source of truth -- the drain reads it in the same tick the run's `finally`
  // releases the slot, before React has committed a state update -- and the
  // state is only the render-facing mirror, exactly like `loadingRef`.
  const [queuedMessages, setQueuedMessages] = useState<QueuedMessage[]>([]);
  const queuedRef = useRef<QueuedMessage[]>([]);

  // Always-current copies of options and loading state for async callbacks.
  // Updated in an effect, never during render. (`messagesRef` is different: it
  // is written by `updateMessages` alone -- the only caller of `setMessages` --
  // so the queue drain can read the just-finished turn synchronously.)
  const optionsRef = useRef(options);
  const messagesRef = useRef(messages);
  // Bumped by every `updateMessages`, and read by the history loader to detect
  // that it has been overtaken. See the comment there -- the conversation-ownership
  // check alone cannot distinguish "untouched" from "a turn started under me".
  const transcriptEpochRef = useRef(0);
  const loadingRef = useRef(isLoading);
  const abortRef = useRef<AbortController | null>(null);
  // Stable ref for the conversation-created callback — avoids re-binding sendMessage.
  const onConversationCreatedRef = useRef(options.onConversationCreated);
  // Same reason: read inside the stream loop, which is created long before the
  // latest render, so a direct capture would fire a stale closure.
  const onArtifactSavedRef = useRef(options.onArtifactSaved);
  // Holds the queue while a human approval is outstanding: the run is
  // interrupted, and dispatching the next turn would start a second run that
  // races the approval. Synced from state by the effect below.
  const pendingHITLRef = useRef(pendingHITL);
  // Assigned by the effect below. `sendMessage` drains the queue by invoking
  // itself, and a direct self-reference inside its own useCallback initialiser
  // would be a temporal-dead-zone read at definition time.
  const sendMessageRef = useRef<
    (content: string, sendOptions?: SendMessageOptions) => Promise<void>
  >(async () => {});
  // Assigned after `hydrateThenRejoin` is defined, below. The delete action
  // re-reads the transcript through it, but that callback is declared later in
  // this hook, and referencing it directly from an earlier useCallback initialiser
  // would be a temporal-dead-zone read.
  const hydrateThenRejoinRef = useRef<
    ((conversationId: string) => Promise<void>) | null
  >(null);

  useEffect(() => {
    optionsRef.current = options;
    onConversationCreatedRef.current = options.onConversationCreated;
    onArtifactSavedRef.current = options.onArtifactSaved;
  }, [options]);

  useEffect(() => {
    loadingRef.current = isLoading;
  }, [isLoading]);

  useEffect(() => {
    pendingHITLRef.current = pendingHITL;
  }, [pendingHITL]);

  const updateMessages = useCallback(
    (
      value:
        | NexusMessage[]
        | ((prev: NexusMessage[]) => NexusMessage[])
    ) => {
      // Every write to the transcript goes through here, so bumping the counter
      // here is what makes an in-flight history load able to notice that it has
      // been overtaken. Ownership alone is not enough: `historyOwnerRef` cannot
      // tell "nobody has touched this conversation" from "the user typed and sent
      // a turn while I was fetching", and the second one is the case where
      // applying the fetched history would delete the message they just sent and
      // the answer already streaming back. The writers invalidate; the loader
      // checks.
      transcriptEpochRef.current += 1;
      // Compute from the ref and write it back synchronously rather than
      // letting React call a functional updater during the next render. The
      // queue drain starts the following turn in the *same tick* the current
      // run finishes, and it reads `messagesRef.current` to build the backend
      // history -- with a render-scheduled updater that read would miss the
      // final streamed patch, so the next turn would be sent without the tail
      // of the answer it is replying to.
      const prev = messagesRef.current;
      const next =
        typeof value === "function"
          ? (value as (p: NexusMessage[]) => NexusMessage[])(prev)
          : value;
      messagesRef.current = next;
      setMessages(next);
    },
    []
  );

  const handleInputChange = (
    e: React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement>
  ) => {
    setInput(e.target.value);
  };

  // ── Queue ────────────────────────────────────────────────────────────────
  // Write the mirror and the ref together; nothing else may mutate the ref
  // alone or the two would disagree about what is pending.

  const setQueue = useCallback((next: QueuedMessage[]) => {
    queuedRef.current = next;
    setQueuedMessages(next);
  }, []);

  const enqueue = useCallback(
    (content: string, options?: SendMessageOptions) => {
      const entry: QueuedMessage = {
        id: `queued-${Date.now()}-${queuedRef.current.length}`,
        content,
        options,
      };
      setQueue([...queuedRef.current, entry]);
    },
    [setQueue]
  );

  const cancelQueued = useCallback(
    (id: string) => {
      setQueue(queuedRef.current.filter((q) => q.id !== id));
    },
    [setQueue]
  );

  const clearQueued = useCallback(() => setQueue([]), [setQueue]);

  /**
   * Start the next queued turn if the slot is free.
   *
   * Called from every run's `finally`, so it runs exactly once per finished
   * run. It deliberately does not start a turn while `loadingRef` is held:
   * `stop()` no longer releases the slot synchronously (that would let a
   * user send and this drain start two runs at once), so the only correct
   * moment to advance the queue is when a run has actually ended.
   */
  const drainQueue = useCallback(() => {
    if (loadingRef.current) return;
    // A pending approval owns the conversation until the human answers.
    if (pendingHITLRef.current) return;
    const next = queuedRef.current[0];
    if (!next) return;
    setQueue(queuedRef.current.slice(1));
    void sendMessageRef.current(next.content, next.options);
  }, [setQueue]);

  /**
   * Adopt the server UUIDs of the turn that just finished — ids only, never
   * content.
   *
   * A freshly-sent turn is persisted by the backend (both rows: the question
   * and the answer), but the POST stream reports only the run id, so the client
   * keeps its own `msg-…` ids unless some later hydrate replaces the list. Every
   * per-message action keys off a server UUID, so without adoption the *first*
   * edit or delete on a fresh turn is silently a no-op: `isServerId` fails, the
   * server call is skipped, and the delete's own re-hydrate then re-adds the
   * rows it was meant to remove. Edit is the damaging version — the resend runs,
   * the browser discards the old turn, and the database keeps it, so "later
   * turns will be discarded" holds only in the UI (observed live: four rows
   * where the transcript showed two).
   *
   * Why ids only. A full re-hydrate here would replace the just-streamed
   * answer with whatever the history endpoint returns, which would drop a
   * partial answer on a lagging read and take an unsaved image bubble with it
   * (images are not persisted). So the tails of the two lists are aligned on
   * this turn — its rows are the last ones written — and each live bubble takes
   * the id of the row that matches it by role *and* exact text.
   *
   * Any mismatch stops the walk rather than continuing: a wrong adoption would
   * aim an action at someone else's row, while a missed one merely leaves the
   * id live, which is the pre-adoption behaviour. `isServerId` rows are skipped,
   * not re-adopted — one that is already a UUID is not this turn's to relabel.
   */
  const adoptPersistedIds = useCallback((rows: NexusMessage[]) => {
    const current = messagesRef.current;
    if (!current.length || !rows.length) return;
    const swaps: Array<[string, string]> = [];
    for (let i = 1; i <= current.length; i += 1) {
      const live = current[current.length - i];
      const row = rows[rows.length - i];
      if (!live || !row) break;
      if (isServerId(live.id)) continue;
      if (live.role !== row.role || live.content !== row.content) break;
      swaps.push([live.id, row.id]);
    }
    if (!swaps.length) return;
    updateMessages((prev) =>
      prev.map((m) => {
        const swap = swaps.find(([oldId]) => oldId === m.id);
        return swap ? { ...m, id: swap[1] } : m;
      })
    );
  }, [updateMessages]);

  const sendMessage = useCallback(
    async (content: string, sendOptions?: SendMessageOptions) => {
      const trimmed = content.trim();
      const imageDataUrl = sendOptions?.imageDataUrl;
      if (!trimmed && !imageDataUrl) return;
      if (loadingRef.current) {
        // A run is already streaming. The backend stream is sequential, so this
        // turn cannot be dispatched now — hold it and let the active run's
        // `finally` start it. Returning here (the old behaviour) silently
        // dropped whatever the user typed while an answer was arriving, which
        // is precisely the moment they are most likely to send a follow-up.
        enqueue(trimmed, sendOptions);
        return;
      }
      // Claim the in-flight slot synchronously. `loadingRef` is normally synced
      // from `isLoading` by an effect, which runs *after* commit -- so between
      // this guard and that effect two `sendMessage` calls in the same tick both
      // saw `false` and both sent, duplicating the user turn. The ref is the
      // guard's source of truth; `setIsLoading` is only the render-facing mirror.
      loadingRef.current = true;

      setError(null);
      setPendingHITL(null);

      const conversationId =
        sendOptions?.conversationId ?? optionsRef.current.conversationId ?? "";
      const mode = sendOptions?.mode ?? optionsRef.current.mode ?? "normal";
      const model = optionsRef.current.model;
      const planPreamble = sendOptions?.planPreamble;
      const reuseUserMessageId = sendOptions?.reuseUserMessageId;

      const userMsgId = `msg-${Date.now()}`;
      const assistantMsgId = `msg-${Date.now() + 1}`;
      const userMsg: NexusMessage = {
        id: userMsgId,
        role: "user",
        content: trimmed,
        imageDataUrl,
        createdAt: new Date().toISOString(),
      };
      const assistantMsg: NexusMessage = {
        id: assistantMsgId,
        role: "assistant",
        content: "",
        model,
      };

      const history = messagesRef.current;
      // A resend that carries `reuseUserMessageId` is re-asking a row that is
      // already the last turn in the list (edit-and-resend keeps the edited
      // row). Appending another optimistic user bubble on top renders the
      // question twice beside the new answer and puts it in the resend payload
      // twice -- the client-side half of the duplicate-turn defect the
      // server's row adoption prevents in the database (measured live
      // 2026-10-24: one persisted row, two bubbles). Regenerate removes the
      // user row before resending, so its reuse id is not found here and it
      // keeps the normal append path.
      const reuseIdx = reuseUserMessageId
        ? history.findIndex((m) => m.id === reuseUserMessageId)
        : -1;
      const turnAlreadyPresent = reuseIdx !== -1 && reuseIdx === history.length - 1;
      if (turnAlreadyPresent) {
        // The row is already in the list with the edited text; only the
        // assistant placeholder is new.
        updateMessages((prev) => [...prev, assistantMsg]);
      } else {
        updateMessages((prev) => [...prev, userMsg, assistantMsg]);
      }
      setIsLoading(true);
      // Cleared per turn, not per mount: a verdict left over from the previous
      // run would be displayed against this run's answer.
      setTurnVerdict(null);
      setStatusLabel(null);

      const controller = new AbortController();
      abortRef.current = controller;

      /**
       * Which localStorage key this turn's run id was filed under, or null if it
       * was never filed.
       *
       * Declared out here because both ends of the turn need it and only the
       * middle can set it: the key is known once the header is read (or once the
       * backend reports a resolved conversation id), and it has to be *cleared*
       * when the stream ends cleanly. A variable scoped to the `try` could not
       * be cleared from the failure paths that matter, which is the direction
       * that silently loses the ability to recover.
       */
      let rememberedUnder: string | null = null;
      // Set when the backend answered 409: a run it still owns is streaming, and
      // this turn never started. Read by `finally` to choose re-attachment over
      // draining the queue.
      let conflicted = false;
      /**
       * Whether this turn asked for human approval. Until it is resolved the
       * answer has not been persisted, so there are no row ids to adopt and a
       * history read would show a transcript missing the text on screen.
       */
      let hitlRequested = false;

      /**
       * Write a streamed message back into the list.
       *
       * Shared by the live turn and the rejoin, and the reason the rejoin can
       * exist at all without duplicating this. `annos.length > 0 ? annos :
       * m.annotations` is deliberate and not a no-op: an annotation-free patch
       * must not clear annotations an earlier patch installed, or a text delta
       * arriving between two annotation frames would erase them.
       */
      const patchMessage = (
        messageId: string,
        content: string,
        annos: NexusAnnotation[]
      ) => {
        updateMessages((prev) =>
          prev.map((m) =>
            m.id === messageId
              ? { ...m, content, annotations: annos.length > 0 ? annos : m.annotations }
              : m
          )
        );
      };

      // One transcript write per painted frame, not per token frame. See
      // `coalescePatches`: the patch carries the whole answer, so coalescing
      // drops nothing, and `flush` below guarantees the last text lands even when
      // the stream ends without a frame to paint it.
      // Text deltas paint immediately (no RAF batching) so tokens stream as
      // fast as the backend emits them. Annotation frames (tool calls,
      // citations, HITL) are infrequent enough that immediate apply is fine.
      const assistantPatcher = coalescePatches((content, annos) =>
        patchMessage(assistantMsgId, content, annos)
      , { immediate: true });
      const patchAssistant = assistantPatcher.patch;

      try {
        // Build multimodal message list — user message may carry an image.
        // When the turn is already the last row (reuse case), `history` ends
        // with it, so appending `userMsg` again would send the question twice.
        const backendMessages = (turnAlreadyPresent ? history : [...history, userMsg]).map((m) => {
          if (m.imageDataUrl) {
            // Multimodal content array for vision
            return {
              role: m.role,
              content: [
                { type: "text", text: m.content || "What's in this image?" },
                {
                  type: "image_url",
                  image_url: { url: m.imageDataUrl },
                },
              ],
            };
          }
          return { role: m.role, content: m.content };
        });

        // Session-recovering fetch, not a bare `fetch`. The chat stream is the
        // one authenticated call that cannot go through the JSON helpers (it
        // consumes a ReadableStream), and it used to use plain `fetch`, which
        // meant an expired access token surfaced as a raw "Backend returned 401"
        // banner instead of the transparent refresh every other call gets. The
        // route now also returns a real 401 rather than a 200 with an error
        // frame, so this recovery path can actually fire.
        const response = await fetchWithSessionRecovery("/api/chat", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          signal: controller.signal,
          body: JSON.stringify({
            messages: backendMessages,
            conversationId,
            mode,
            // The picker's selection. It reaches the hook through
            // `useNexusChat({ model })`; without this line the whole picker was
            // cosmetic, because the backend chose the model and the request
            // never carried one.
            ...(model ? { model } : {}),
            ...(planPreamble ? { planPreamble } : {}),
            ...(reuseUserMessageId
              ? { reuseUserMessageId }
              : {}),
          }),
        });

        if (response.status === 409) {
          // The backend still holds this conversation's stream slot. That is not
          // a failed turn: a local Stop aborts *our* socket, never the server's
          // run, so the next send lands while that run is still executing.
          //
          // This turn was never started, so its optimistic bubbles are removed
          // rather than left as an empty answer, the text goes back into the
          // composer, and `finally` re-attaches to the run that is still
          // producing — the same hydrate-and-rejoin path a page reload uses, so
          // the arriving answer is shown instead of an error.
          conflicted = true;
          updateMessages((prev) =>
            prev.filter((m) => m.id !== userMsgId && m.id !== assistantMsgId)
          );
          setInput((prev) => (prev ? prev : trimmed));
          setStatusLabel("The previous answer is still running — reconnecting…");
          return;
        }
        if (!response.ok || !response.body) {
          throw await chatErrorFromResponse(response);
        }

        const runId = readRunId(response);
        if (runId && conversationId) {
          rememberRun(conversationId, runId);
          rememberedUnder = conversationId;
        }

        const streamResult = await consumeChatStream(response.body, {
          patch: patchAssistant,
          onError: (message) => setError(new Error(message)),
          onHitlRequest: (data) => {
            hitlRequested = true;
            setPendingHITL(data);
          },
          onArtifactSaved: (artifact) =>
            onArtifactSavedRef.current?.(artifact),
          onTurnVerdict: setTurnVerdict,
          onStatus: setStatusLabel,
          onModelResolved: (resolvedModel) => {
            // Report the model that actually answered, not the picker's label:
            // a provider fallback must be visible in the transcript rather than
            // hidden behind the group the user chose.
            updateMessages((prev) =>
              prev.map((m) =>
                m.id === assistantMsgId ? { ...m, model: resolvedModel } : m
              )
            );
          },
          onConversationCreated: (id) => {
            // A conversation the client did not name: the run id read off the
            // header had no key to be filed under, so file it under the
            // resolved one now. That is what lets a reload after the very first
            // turn re-attach -- the id arrives on the last frame, which is
            // before the stream closes, so nothing is missed.
            if (conversationId) return;
            if (runId) {
              rememberRun(id, runId);
              rememberedUnder = id;
            }
            onConversationCreatedRef.current?.(id);
          },
        }).finally(assistantPatcher.flush);

        // Reaching this line means the stream closed at the backend's end, so the
        // run is over and there is nothing left to re-attach to. Every failure
        // path -- a dropped connection, a rejected fetch, an abort -- jumps past
        // it and deliberately leaves the stored id in place, because a run the
        // browser stopped watching for its own reasons is still going, and
        // forgetting its id is precisely the case rejoin exists to cover.
        forgetRun(rememberedUnder);

        // Adopt the row ids this run just persisted (see adoptPersistedIds).
        // Gated three ways, each for a case where the history read would not
        // contain this turn's rows: a `3:` error means no answer row was ever
        // written, a pending approval means the run is paused before writing
        // one, and an image turn's picture does not survive a history read.
        // `targetId` falls back to the id the backend just resolved for a
        // brand-new conversation; the on-screen check (repeated after the
        // read) keeps a mid-stream conversation switch from adopting one
        // conversation's rows into another's transcript.
        // Not awaited on purpose: the turn is already correct on screen, and
        // holding the send's completion — and therefore `isLoading`, and the
        // `finally` below that releases any queued follow-up — hostage to a
        // history read deadlocks whenever that endpoint is slow or parked,
        // which is exactly the situation the one-writer tests stage.
        if (!streamResult.error && !hitlRequested && !imageDataUrl) {
          const targetId = conversationId || rememberedUnder;
          if (targetId && optionsRef.current.conversationId === targetId) {
            void (async () => {
              try {
                const detail = await fetchConversation(targetId);
                if (optionsRef.current.conversationId === targetId) {
                  adoptPersistedIds(messagesFromHistory(detail.messages));
                }
              } catch {
                // Best-effort by design: a failed read leaves the ids live,
                // which is exactly how every turn behaved before adoption.
              }
            })();
          }
        }
      } catch (err) {
        if (err instanceof Error && err.name === "AbortError") return;
        setError(err instanceof Error ? err : new Error("Chat stream failed"));
      } finally {
        setIsLoading(false);
        loadingRef.current = false;
        abortRef.current = null;
        if (conflicted) {
          // Deliberately does not drain the queue: the backend slot is still
          // held by the other run, so starting the next queued turn now would
          // 409 again and loop. Re-attach instead, which is the only action that
          // shows the user what is actually happening. The queue survives — its
          // entries are still in `queuedRef` — and drains after the next run
          // that does start.
          const target = conversationId;
          if (target) void hydrateThenRejoinRef.current?.(target);
          return;
        }
        // Advance the queue, if any. This is the single place a run releases the
        // slot, so it is the single place the next turn may start; releasing it
        // in `stop()` instead would race an aborted run's own teardown.
        drainQueue();
      }
    },
    [updateMessages, enqueue, drainQueue, adoptPersistedIds]
  );

  // The drain calls back into `sendMessage`; publish the stable instance so it
  // can be reached without a self-reference in the callback's own initialiser.
  useEffect(() => {
    sendMessageRef.current = sendMessage;
  }, [sendMessage]);

  const handleSubmit = (e?: React.FormEvent) => {
    if (e) e.preventDefault();
    const value = input.trim();
    if (!value) return;
    setInput("");
    void sendMessage(value);
  };

  const stop = useCallback(() => {
    // Abort only. This used to release the in-flight guard synchronously so a
    // stop-then-send was not swallowed, but with a queue that is both
    // unnecessary and unsafe: a send issued before the abort settles is
    // enqueued, and releasing the slot here would let that send start while the
    // aborted run's own `finally` also drains the queue — two concurrent runs.
    // The aborted fetch rejects promptly and its `finally` does the release.
    abortRef.current?.abort();
    abortRef.current = null;
  }, []);

  const reload = useCallback(async () => {
    // Supersede whatever stream (if any) is still running. As in `stop`, the
    // release happens in the aborted run's `finally`, not here — and the resend
    // below is enqueued and started by that same drain.
    abortRef.current?.abort();
    abortRef.current = null;
    setError(null);
    // Drop queued follow-ups: they were composed against the answer being
    // regenerated, so sending them next would answer a reply the user has just
    // rejected. The resend itself is appended to the now-empty queue.
    clearQueued();

    const msgs = messagesRef.current;
    const lastUser = [...msgs].reverse().find((m) => m.role === "user");
    if (!lastUser) return;
    const idx = msgs.findIndex((m) => m.id === lastUser.id);
    updateMessages(() => msgs.slice(0, idx + 1));
    await sendMessage(lastUser.content);
  }, [sendMessage, updateMessages, clearQueued]);

  // ── Message actions (delete / edit / regenerate / branch) ────────────────
  /**
   * Delete a turn. Server-side first, then the list is rebuilt from the
   * authoritative rows.
   *
   * The cascade count the backend returns is deliberately not used to trim the
   * list by arithmetic: the server deletes by ``created_at`` order and the
   * client's list may have been edited, so slicing by a count can cut the wrong
   * rows. Re-hydrating is exact, and it is the transcript's single writer
   * (§9.28) rather than a second one.
   */
  const removeMessage = useCallback(
    async (messageId: string) => {
      const conversationId = optionsRef.current.conversationId;
      if (isServerId(messageId) && conversationId) {
        setError(null);
        try {
          await apiDeleteMessage(conversationId, messageId);
        } catch (err) {
          setError(err instanceof Error ? err : new Error("Delete failed"));
          throw err;
        }
      }
      // The verdict has no message id of its own (the backend emits it after
      // the row exists), so nothing else ties its lifetime to this list. The
      // cascade can remove the very answer it describes, so it is cleared here
      // or it floats over whatever the transcript shows next -- down to the
      // empty welcome screen.
      setTurnVerdict(null);
      setStatusLabel(null);
      // A client-only id was never persisted, so removing it locally is the
      // whole job. For a server id, re-read so the reply the cascade removed
      // disappears too.
      if (conversationId) {
        await hydrateThenRejoinRef.current?.(conversationId);
      } else {
        updateMessages((prev) => prev.filter((m) => m.id !== messageId));
      }
    },
    [updateMessages]
  );

  /**
   * Edit a user turn and resend it (time-travel).
   *
   * The backend truncates everything after the edited message, so the client
   * does the same *before* resending: the resend is built from
   * ``messagesRef.current``, and a stale assistant reply left in the list would
   * be sent back to the model as context for a question it never answered.
   */
  const editAndResend = useCallback(
    async (messageId: string, content: string) => {
      const conversationId = optionsRef.current.conversationId;
      const trimmed = content.trim();
      if (!trimmed) return;

      const msgs = messagesRef.current;
      const idx = msgs.findIndex((m) => m.id === messageId);
      if (idx === -1) return;

      if (isServerId(messageId) && conversationId) {
        setError(null);
        try {
          await apiEditMessage(conversationId, messageId, trimmed);
        } catch (err) {
          setError(err instanceof Error ? err : new Error("Edit failed"));
          throw err;
        }
      }
      // Truncate locally to the edited turn. The backend already did this
      // server-side; doing it here keeps the resend's context identical to what
      // the transcript shows.
      //
      // A persisted row stays in the list (with the new text) and the resend
      // re-asks it by id -- `sendMessage` detects that the reuse id is the last
      // turn and does not append a second bubble. A live (client-id) row is
      // dropped instead, because it was never persisted: the resend's own
      // optimistic bubble becomes the single copy of the edited question.
      updateMessages(() =>
        isServerId(messageId)
          ? [...msgs.slice(0, idx), { ...msgs[idx], content: trimmed }]
          : msgs.slice(0, idx)
      );
      // Resend through the normal send path, which appends a fresh user turn and
      // its answer. `messagesRef.current` is now the truncated list, so the
      // request context is exactly the edited conversation. The server already
      // holds this turn -- it was edited in place, not deleted -- so its id goes
      // back with the resend and the row is adopted instead of stored twice.
      await sendMessageRef.current?.(trimmed, {
        ...(isServerId(messageId) ? { reuseUserMessageId: messageId } : {}),
      });
    },
    [updateMessages]
  );

  /**
   * Regenerate an assistant reply.
   *
   * The backend deletes the stale reply and returns the user prompt that
   * produced it, so the re-run is driven by the *stored* text rather than the
   * client's copy of it — which is what makes a regenerate after a reload work
   * at all. The prompt is removed from the list before resending so the resend
   * does not duplicate the user bubble.
   */
  const regenerateMessage = useCallback(
    async (messageId: string) => {
      const conversationId = optionsRef.current.conversationId;
      if (!conversationId) return;
      if (!isServerId(messageId)) {
        // A live (unpersisted) reply: fall back to the local-only reload, which
        // re-asks the last user turn.
        await reload();
        return;
      }
      setError(null);
      let prompt = "";
      let promptId: string | undefined;
      try {
        const result = await apiRegenerateMessage(conversationId, messageId);
        prompt = result.prompt;
        // The endpoint returns the stored prompt's id along with its text, so
        // the resend can adopt that row. Without it the route appended a second
        // copy of the question -- the duplicate measured live.
        promptId = result.user_message_id || undefined;
      } catch (err) {
        setError(err instanceof Error ? err : new Error("Regenerate failed"));
        throw err;
      }
      if (!prompt) return;
      // Drop the ask *and* the stale reply it produced. The backend removed the
      // reply; removing the preceding user turn here lets `sendMessage` append a
      // clean one, so the transcript ends with exactly one prompt and one answer.
      const msgs = messagesRef.current;
      const idx = msgs.findIndex((m) => m.id === messageId);
      updateMessages(() => {
        if (idx === -1) return msgs;
        let cut = idx;
        // Walk back to the user turn that prompted this reply.
        while (cut > 0 && msgs[cut - 1].role !== "user") cut -= 1;
        const userIdx = msgs[cut - 1]?.role === "user" ? cut - 1 : idx;
        return msgs.slice(0, userIdx);
      });
      await sendMessageRef.current?.(prompt, {
        ...(promptId ? { reuseUserMessageId: promptId } : {}),
      });
    },
    [updateMessages, reload]
  );

  /**
   * Branch from a message: fork the conversation up to and including it.
   *
   * The fork is a *new* conversation; the callback hands its id back so the page
   * can select it. Returns the new conversation id, or null if there was nothing
   * to branch.
   */
  /**
   * Branch from a specific message.
   *
   * Returns the whole forked `Conversation`, not just its id, because the
   * caller has to seed the conversation list with it. Setting only the active
   * id left the fork *absent* from the list the auto-titler reads, and absence
   * looked identical to "untitled" — so the title the backend built
   * (`"<parent> (Branch: …)"`) was immediately overwritten with the first user
   * message, and every branch came out mis-named.
   */
  const branchFromMessage = useCallback(async (messageId: string) => {
    const conversationId = optionsRef.current.conversationId;
    if (!conversationId) return null;
    setError(null);
    try {
      return await forkConversation(
        conversationId,
        isServerId(messageId) ? messageId : null
      );
    } catch (err) {
      setError(err instanceof Error ? err : new Error("Branch failed"));
      throw err;
    }
  }, []);

  // Extract typed annotations from messages
  const getAnnotations = useCallback(
    (message: NexusMessage): NexusAnnotation[] => message.annotations ?? [],
    []
  );

  const getToolCalls = useCallback(
    (message: NexusMessage) =>
      getAnnotations(message).filter(
        (a): a is ToolCallAnnotation => a.type === "tool_call"
      ),
    [getAnnotations]
  );

  const getToolResults = useCallback(
    (message: NexusMessage) =>
      getAnnotations(message).filter(
        (a): a is ToolResultAnnotation => a.type === "tool_result"
      ),
    [getAnnotations]
  );

  const getCitations = useCallback(
    (message: NexusMessage) =>
      getAnnotations(message).filter(
        (a): a is CitationAnnotation => a.type === "citation"
      ),
    [getAnnotations]
  );

  const getReasoningBlocks = useCallback(
    (message: NexusMessage) =>
      getAnnotations(message).filter(
        (a): a is ReasoningAnnotation => a.type === "reasoning"
      ),
    [getAnnotations]
  );

  const clearMessages = useCallback(() => {
    updateMessages(() => []);
    // Switching or clearing a conversation must not leave turns queued against
    // the old one — they would fire into the new conversation's stream.
    clearQueued();
    setPendingHITL(null);
    pendingHITLRef.current = null;
    setTurnVerdict(null);
    setStatusLabel(null);
    setError(null);
  }, [updateMessages, clearQueued]);

  // ── Rejoin ───────────────────────────────────────────────────────────────
  /**
   * Re-attach to a run that is still going, and replay the answer into the view.
   *
   * This is the client half of the run decoupling. The backend keeps producing
   * frames whether or not anybody is reading, so the answer to "what happened to
   * the answer I was watching?" is a second read of the same run rather than a
   * resend — which matters, because a resend is a *different answer*: it spends a
   * second run, charges a second time, and produces text that does not match the
   * one the user already saw.
   *
   * ## What this reconstructs, precisely
   *
   * The recovered assistant message only. The run log holds the frames, and the
   * frames do not include the user turn — that is a message row the backend
   * wrote, and the chat view does not load conversation history at all (a
   * pre-existing gap, out of scope here). Inventing a user bubble to sit above
   * the recovered answer would be fabricating conversation, so the answer
   * arrives alone.
   *
   * ## Why no `Last-Event-ID`
   *
   * The backend and the BFF both honour a cursor, and it is tested there. This
   * hook never sends one: after a reload the in-memory frame list is empty, so
   * the first frame *is* the one that needs re-reading, and resuming from a
   * cursor carried across the reload would skip exactly the part of the answer
   * the user is missing. A full replay is only wasteful for a long answer; a
   * wrong cursor is a hole in it.
   */
  const rejoinRun = useCallback(
    async (conversationId: string, runId: string, signal: AbortSignal) => {
      // Declared up front so the catch can remove a bubble that was created, and
      // is a no-op when the failure happened before one existed. Without this the
      // probe's own transport failure escaped the handler below as an unhandled
      // rejection -- the hook reported no error, kept no bubble, and looked
      // exactly like a run that had nothing to replay.
      let messageId: string | null = null;

      try {
        // Ask first, render later. The `POST`/`GET` responses differ in exactly
        // one way that matters here: the GET carries the id of the message row a
        // finished run persisted, so the transcript can be checked for the answer
        // before a bubble is created for it.
        const probe = await fetchWithSessionRecovery(
          `/api/chat?conversationId=${encodeURIComponent(
            conversationId
          )}&runId=${encodeURIComponent(runId)}`,
          { method: "GET", signal, headers: { Accept: "text/plain" } }
        );

        // A run the backend no longer has (retention, or a different environment)
        // is not an error to show the user about their own conversation: forget it
        // and leave the transcript alone, because an empty assistant message is
        // worse than no message.
        if (probe.status === 404) {
          forgetRun(conversationId);
          return;
        }
        if (!probe.ok || !probe.body) {
          throw new Error(`Rejoin failed: ${probe.statusText}`);
        }

        // The run finished and wrote its answer, and that answer is already on
        // screen from the history load. Replaying it would render it twice.
        //
        // The "is there an id" question is asked inside `hasAnswer` and *only*
        // there. It used to be asked here as well (`persisted && hasAnswer(...)`),
        // which made the guard in `hasAnswer` unreachable -- and an unreachable
        // guard is a second place to be wrong rather than a safety net: the test
        // proving "no id never means already rendered" passed with the guard
        // deleted, because the call site was already short-circuiting. One place
        // decides, and it is the function whose name is the decision.
        if (hasAnswer(messagesRef.current, readMessageId(probe))) {
          forgetRun(conversationId);
          await probe.body.cancel();
          return;
        }

        messageId = `msg-rejoined-${runId.slice(0, 8)}`;
        // A second name for the same value, and the reason is a `let`.
        //
        // `messageId` is the catch block's "did we get far enough to render a
        // bubble" flag, so it is nullable and mutable; every closure below reads
        // it, and TypeScript will not carry a narrowing across a closure over a
        // `let` — which it correctly reports as `id: string | null` against
        // `NexusMessage`. Binding the id to a `const` also removes a real hazard
        // rather than only satisfying the checker: nothing between the two can
        // reassign it out from under a frame that is still being applied.
        const rejoinedId = messageId;

        // The bubble is created before the stream is read so the answer streams
        // into a message that exists. Appending it after the frames start means an
        // empty gap in the transcript while the durable copy is read, which on a
        // slow connection reads as "nothing was recovered".
        updateMessages((prev) =>
          prev.some((m) => m.id === rejoinedId)
            ? prev
            : [
                ...prev,
                {
                  id: rejoinedId,
                  role: "assistant",
                  content: "",
                  model: optionsRef.current.model,
                },
              ]
        );

        // The rejoin replays a finished run, so it can emit its whole stream of
        // frames in one burst — the same coalescing applies, and `finally` writes
        // the final text even if the replay is cut short.
        const rejoinPatcher = coalescePatches((content, annos) =>
          updateMessages((prev) =>
            prev.map((m) =>
              m.id === rejoinedId
                ? {
                    ...m,
                    content,
                    annotations: annos.length > 0 ? annos : m.annotations,
                  }
                : m
            )
          )
        );

        await consumeChatStream(probe.body, {
          patch: rejoinPatcher.patch,
          onArtifactSaved: (artifact) =>
            onArtifactSavedRef.current?.(artifact),
          onTurnVerdict: setTurnVerdict,
          onStatus: setStatusLabel,
          onModelResolved: (resolvedModel) =>
            updateMessages((prev) =>
              prev.map((m) =>
                m.id === rejoinedId ? { ...m, model: resolvedModel } : m
              )
            ),
          // A rejoin does not surface `hitl_request` as pending state: the run
          // was interrupted for approval before the page went away, and the card
          // it raises belongs to a turn this view has no user message for. It
          // stays in the annotation list, so the transcript records that the
          // turn stopped for a human rather than silently reading as complete.
        }).finally(rejoinPatcher.flush);

        // The replay reached the backend's end, so the run is finished.
        forgetRun(conversationId);
      } catch (err) {
        if (err instanceof Error && err.name === "AbortError") return;
        // The run is probably still going — a network blip, or the backend was
        // unreachable. The stored id is kept, so a later reload tries again, and
        // the bubble is removed rather than left empty.
        if (messageId) {
          const failed = messageId;
          updateMessages((prev) => prev.filter((m) => m.id !== failed));
        }
        setError(
          err instanceof Error
            ? err
            : new Error("Could not re-attach to the running answer.")
        );
      }
    },
    [updateMessages]
  );

  // ── History / rejoin ordering ───────────────────────────────────────────
  /**
   * The conversation id whose history is currently on screen.
   *
   * A ref rather than state because it gates a *write*, not a render: the guard's
   * whole purpose is to stop a history load that resolves late from replacing a
   * transcript that has since moved on.
   */
  const historyOwnerRef = useRef<string>("");
  // Render-facing mirror of "a conversation's messages are being fetched". The
  // page shows a skeleton on this, and it used to keep its own flag around its
  // own loader -- which is the second writer this ordering exists to remove.
  const [isHydrating, setIsHydrating] = useState(false);

  /**
   * Re-attach to a run, unless its answer is already in the transcript.
   *
   * The page loads conversation history when a conversation is selected, and that
   * load *replaces* the whole message list. Two writers to one list is the hazard:
   *
   * 1. **A late history load erases a live rejoin.** Selecting a conversation
   *    fires both. If the history request resolves after the rejoin has streamed
   *    frames, `setMessages(history)` drops the bubble mid-answer and the user
   *    watches a recovered answer disappear. `historyOwnerRef` makes the load
   *    yield once the transcript belongs to this conversation.
   * 2. **A finished run is shown twice.** The replay starts at frame 0, so a run
   *    that completed while the page was closed replays an answer the history has
   *    already rendered. The backend states which message row the run persisted
   *    (`X-Nexus-Message-Id`); if the transcript already holds that id there is
   *    nothing to recover, so the run id is retired instead.
   *
   * Order is history first, then rejoin. The other order is the one that loses
   * data: the rejoin's bubble would be created against an empty list and then
   * replaced by the history, or the history would land on a bubble the replay had
   * already patched.
   */
  const hydrateThenRejoin = useCallback(
    async (conversationId: string) => {
      // Claim the transcript immediately, before any await. A conversation switch
      // with no history load yet must invalidate the previous one's in-flight
      // load, and that is the only moment the claim can be made.
      historyOwnerRef.current = conversationId;
      // The write-generation this load was started from. Anything that writes to
      // the transcript while we are waiting -- a turn the user just started, a
      // second conversation load -- bumps it, and the load yields rather than
      // replace newer content with older content.
      let epoch = transcriptEpochRef.current;
      setIsHydrating(true);

      // Two independent reasons to stop, and neither subsumes the other: the
      // owner check catches "this is a different conversation now", the epoch
      // check catches "this is the same conversation but the transcript moved".
      const overtaken = () =>
        historyOwnerRef.current !== conversationId ||
        transcriptEpochRef.current !== epoch;

      try {
        const detail = await fetchConversation(conversationId);
        // Someone else's load, or a conversation switch since: applying this
        // would put conversation A's messages under conversation B's name.
        if (overtaken()) return;
        updateMessages(messagesFromHistory(detail.messages));
        // Re-baseline before the check below. Applying the history is itself a
        // write, so without this the loader would immediately outdate itself and
        // the rejoin it is supposed to be followed by would never run -- a guard
        // that reads as "someone else took over" every single time, which is the
        // worst kind: it passes nothing and explains nothing.
        epoch = transcriptEpochRef.current;
      } catch (err) {
        // A failed history load is not an error the user needs to see: the chat
        // still works, and a rejoin below will render the answer on its own.
        console.warn("Failed to load conversation history:", err);
      } finally {
        // Only the owner clears it. A superseded load leaving this set would show
        // a skeleton forever, since nobody else would turn it off.
        if (historyOwnerRef.current === conversationId) setIsHydrating(false);
      }

      // Overtaken by a turn that started while we were fetching: joining now
      // would put a second run's frames into a conversation whose first run is
      // still streaming, which the backend answers by 409ing one of them.
      if (overtaken()) return;
      const runId = readRun(conversationId);
      if (!runId) return;

      const controller = new AbortController();
      abortRef.current = controller;
      try {
        await rejoinRun(conversationId, runId, controller.signal);
      } finally {
        if (abortRef.current === controller) abortRef.current = null;
      }
    },
    [updateMessages, rejoinRun]
  );

  // Publish the stable instance for the earlier-declared message actions, which
  // cannot close over it directly (see `hydrateThenRejoinRef`).
  useEffect(() => {
    hydrateThenRejoinRef.current = hydrateThenRejoin;
  }, [hydrateThenRejoin]);

  useEffect(() => {
    const conversationId = optionsRef.current.conversationId;
    if (!conversationId) return;
    // A live turn already owns this conversation's stream. Hydrating or joining as
    // well would replace the transcript out from under the in-flight answer, and
    // put two runs' worth of requests against one backend thread slot.
    if (loadingRef.current || abortRef.current) return;
    void hydrateThenRejoin(conversationId);
    // `hydrateThenRejoin` is in the dependency list and that is correct, not a
    // warning to suppress: it is a useCallback over `[updateMessages]` and
    // `rejoinRun`, which are themselves referentially stable for the life of the
    // hook, so the effect fires on a conversation change only. An earlier version
    // had a disable directive here for an omission that no longer applies.
  }, [options.conversationId, hydrateThenRejoin]);

  // HITL approval/rejection
  const resolveHITL = useCallback(
    async (
      action: "approve" | "reject" | "modify",
      data?: Record<string, unknown>
    ) => {
      const request = pendingHITL;
      if (!request) return;
      setError(null);
      try {
        await sendHITLFeedback({
          threadId: request.thread_id,
          action,
          data: data ?? (action === "modify" ? { approved: true } : {}),
        });
        // Only retire the card once the backend has actually recorded the
        // decision. Clearing it in a `finally` destroyed the Approve/Deny UI on
        // any transient failure (offline, 5xx, timeout), silently dropping a
        // human approval with no error and no way to retry it -- the one flow in
        // this product that must not be lossy.
        setPendingHITL(null);
        // The approval was holding the queue (drainQueue refuses to run while a
        // request is outstanding). Update the ref directly because the mirroring
        // effect has not run yet.
        pendingHITLRef.current = null;
        // The backend resumed the graph inline and persisted the answer, but it
        // does not stream the resumed turn back to this client. Without a reload
        // the card cleared and the user saw nothing until a manual refresh --
        // the approval "succeeded" and produced no visible result. Reload through
        // the same single-writer path the transcript already uses, and only when
        // this conversation is the one on screen (otherwise the answer will load
        // when the user switches back).
        if (optionsRef.current.conversationId === request.thread_id) {
          await hydrateThenRejoin(request.thread_id);
        }
        // Drain after the reload so a queued follow-up appends to the recovered
        // answer instead of racing it.
        drainQueue();
      } catch (err) {
        const error =
          err instanceof Error
            ? err
            : new Error("Could not submit the approval. Please try again.");
        setError(error);
        // Re-throw so the approval card can tell a failed submit from a
        // successful one. Swallowing the error here made `ChatArea`'s failure
        // guard unreachable -- it awaited this call and, seeing no throw, marked
        // the request resolved and collapsed the card, so a *failed* approval
        // silently retired the very UI the hook deliberately keeps by not
        // clearing `pendingHITL`. The card retires only on a resolution, which
        // is exactly when the decision actually landed.
        throw error;
      }
    },
    [pendingHITL, drainQueue, hydrateThenRejoin]
  );

  /**
   * Generate an image from a text prompt (free, key-less, via Pollinations).
   *
   * A separate path from `sendMessage` on purpose: this is not a graph turn, it
   * spends no tokens, and there is no SSE stream to consume — routing it through
   * the chat stream would open a run whose answer never arrives. The image is
   * carried on the assistant message's `imageDataUrl`, so a base64 body is never
   * duplicated into the markdown `content`.
   *
   * The turn is client-only: it is not persisted server-side (there is no
   * message row for a generated image), so a reload drops it. That is a
   * deliberate limit of the free path, not an oversight — persisting it would
   * need a new backend write the chat transcript was never built to accept.
   */
  const generateImage = useCallback(
    async (prompt: string) => {
      const trimmed = prompt.trim();
      if (!trimmed) return;

      const userMsgId = `msg-${Date.now()}`;
      const assistantMsgId = `msg-${Date.now() + 1}`;
      const userMsg: NexusMessage = {
        id: userMsgId,
        role: "user",
        content: `🎨 ${trimmed}`,
        createdAt: new Date().toISOString(),
      };
      const assistantMsg: NexusMessage = {
        id: assistantMsgId,
        role: "assistant",
        content: "",
        model: "pollinations/flux",
      };
      updateMessages((prev) => [...prev, userMsg, assistantMsg]);
      setIsLoading(true);
      setError(null);

      try {
        const res = await fetchWithSessionRecovery("/api/images/generate", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ prompt: trimmed }),
        });
        if (!res.ok) {
          let detail = `Image generation failed (${res.status})`;
          try {
            detail = ((await res.json()) as { detail?: string }).detail ?? detail;
          } catch {
            /* non-JSON error body: keep the status-based detail */
          }
          throw new Error(detail);
        }
        const data = (await res.json()) as {
          image_data_url: string;
          model: string;
        };
        updateMessages((prev) =>
          prev.map((m) =>
            m.id === assistantMsgId
              ? {
                  ...m,
                  content: "Here's the image you asked for.",
                  imageDataUrl: data.image_data_url,
                  model: data.model,
                }
              : m
          )
        );
      } catch (err) {
        const message =
          err instanceof Error ? err.message : "Image generation failed";
        setError(new Error(message));
        updateMessages((prev) =>
          prev.map((m) =>
            m.id === assistantMsgId
              ? { ...m, content: `I couldn't generate that image. ${message}` }
              : m
          )
        );
      } finally {
        setIsLoading(false);
      }
    },
    [updateMessages]
  );

  return {
    messages,
    isHydrating,
    input,
    handleInputChange,
    handleSubmit,
    sendMessage,
    generateImage,
    isLoading,
    error,
    setMessages: updateMessages,
    clearMessages,
    stop,
    reload,
    removeMessage,
    editAndResend,
    regenerateMessage,
    branchFromMessage,
    pendingHITL,
    resolveHITL,
    turnVerdict,
    statusLabel,
    queuedMessages,
    cancelQueued,
    clearQueued,
    getToolCalls,
    getToolResults,
    getCitations,
    getReasoningBlocks,
  };
}