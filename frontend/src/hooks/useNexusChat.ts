/**
 * Nexus AI – useNexusChat hook
 * Typed streaming chat state for Nexus conversations:
 *  - streams assistant replies from the /api/chat data-stream proxy
 *  - surfaces tool calls, citations, and reasoning as typed annotations
 *  - manages human-in-the-loop (HITL) approval requests
 */
"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { fetchWithSessionRecovery, sendHITLFeedback } from "@/lib/api";
import { consumeChatStream, readRunId } from "@/lib/chatStream";

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
}

export interface UseNexusChatOptions {
  conversationId?: string;
  userId?: string;
  mode?: "normal" | "agent" | "code" | "research";
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
  mode?: "normal" | "agent" | "code" | "research";
  conversationId?: string;
  /** base64 data URL for an image the user has attached */
  imageDataUrl?: string;
  /**
   * Approved-plan preamble: when an approved plan is executed, the backend
   * prepends this text to the agent system prompt so the run follows the
   * plan's approved steps (Plan mode execution reuses the agent stream).
   */
  planPreamble?: string;
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

function readRun(conversationId: string): string | null {
  if (!conversationId) return null;
  try {
    const raw = window.localStorage.getItem(runStorageKey(conversationId));
    return raw && UUID_RE.test(raw) ? raw : null;
  } catch {
    return null;
  }
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
      updateMessages((prev) => [...prev, userMsg, assistantMsg]);
      setIsLoading(true);
      // Cleared per turn, not per mount: a verdict left over from the previous
      // run would be displayed against this run's answer.
      setTurnVerdict(null);

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

      const patchAssistant = (content: string, annos: NexusAnnotation[]) =>
        patchMessage(assistantMsgId, content, annos);

      try {
        // Build multimodal message list — user message may carry an image
        const backendMessages = [...history, userMsg].map((m) => {
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
            ...(planPreamble ? { planPreamble } : {}),
          }),
        });

        if (!response.ok || !response.body) {
          throw new Error(`Chat API error: ${response.statusText}`);
        }

        const runId = readRunId(response);
        if (runId && conversationId) {
          rememberRun(conversationId, runId);
          rememberedUnder = conversationId;
        }

        await consumeChatStream(response.body, {
          patch: patchAssistant,
          onError: (message) => setError(new Error(message)),
          onHitlRequest: (data) => setPendingHITL(data),
          onArtifactSaved: (artifact) =>
            onArtifactSavedRef.current?.(artifact),
          onTurnVerdict: setTurnVerdict,
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
        });

        // Reaching this line means the stream closed at the backend's end, so the
        // run is over and there is nothing left to re-attach to. Every failure
        // path -- a dropped connection, a rejected fetch, an abort -- jumps past
        // it and deliberately leaves the stored id in place, because a run the
        // browser stopped watching for its own reasons is still going, and
        // forgetting its id is precisely the case rejoin exists to cover.
        forgetRun(rememberedUnder);
      } catch (err) {
        if (err instanceof Error && err.name === "AbortError") return;
        setError(err instanceof Error ? err : new Error("Chat stream failed"));
      } finally {
        setIsLoading(false);
        loadingRef.current = false;
        abortRef.current = null;
        // Advance the queue, if any. This is the single place a run releases the
        // slot, so it is the single place the next turn may start; releasing it
        // in `stop()` instead would race an aborted run's own teardown.
        drainQueue();
      }
    },
    [updateMessages, enqueue, drainQueue]
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
      const messageId = `msg-rejoined-${runId.slice(0, 8)}`;

      // The bubble is created before the request so the answer streams into a
      // message that exists. Appending it after the response means an empty gap
      // in the transcript while the durable copy is fetched, which on a slow
      // connection reads as "nothing was recovered".
      updateMessages((prev) =>
        prev.some((m) => m.id === messageId)
          ? prev
          : [
              ...prev,
              {
                id: messageId,
                role: "assistant",
                content: "",
                model: optionsRef.current.model,
              },
            ]
      );

      const dropBubble = () =>
        updateMessages((prev) => prev.filter((m) => m.id !== messageId));

      try {
        const response = await fetchWithSessionRecovery(
          `/api/chat?conversationId=${encodeURIComponent(
            conversationId
          )}&runId=${encodeURIComponent(runId)}`,
          { method: "GET", signal, headers: { Accept: "text/plain" } }
        );

        // A run the backend no longer has (retention, or a different
        // environment) is not an error to show the user about their own
        // conversation: forget it and remove the empty bubble, because an empty
        // assistant message is worse than no message.
        if (response.status === 404) {
          forgetRun(conversationId);
          dropBubble();
          return;
        }
        if (!response.ok || !response.body) {
          throw new Error(`Rejoin failed: ${response.statusText}`);
        }

        await consumeChatStream(response.body, {
          patch: (content, annos) =>
            updateMessages((prev) =>
              prev.map((m) =>
                m.id === messageId
                  ? {
                      ...m,
                      content,
                      annotations: annos.length > 0 ? annos : m.annotations,
                    }
                  : m
              )
            ),
          onArtifactSaved: (artifact) =>
            onArtifactSavedRef.current?.(artifact),
          onTurnVerdict: setTurnVerdict,
          // A rejoin does not surface `hitl_request` as pending state: the run
          // was interrupted for approval before the page went away, and the card
          // it raises belongs to a turn this view has no user message for. It
          // stays in the annotation list, so the transcript records that the
          // turn stopped for a human rather than silently reading as complete.
        });

        // The replay reached the backend's end, so the run is finished.
        forgetRun(conversationId);
      } catch (err) {
        if (err instanceof Error && err.name === "AbortError") return;
        // The run is probably still going — a network blip, or the backend was
        // unreachable. The stored id is kept, so a later reload tries again, and
        // the bubble is removed rather than left empty.
        dropBubble();
        setError(
          err instanceof Error
            ? err
            : new Error("Could not re-attach to the running answer.")
        );
      }
    },
    [updateMessages]
  );

  useEffect(() => {
    const conversationId = optionsRef.current.conversationId;
    if (!conversationId) return;
    // A live turn already owns this conversation's stream. Joining as well would
    // put the same frames into a second bubble, and two runs' worth of requests
    // against one backend thread slot.
    if (loadingRef.current || abortRef.current) return;
    const runId = readRun(conversationId);
    if (!runId) return;

    const controller = new AbortController();
    void rejoinRun(conversationId, runId, controller.signal);
    // `rejoinRun` is in the dependency list and that is correct, not a warning to
    // suppress: it is a useCallback over `[updateMessages]`, which is itself
    // `useCallback([])`, so it is referentially stable for the life of the hook
    // and the effect fires on a conversation change only. An earlier version had
    // a disable directive here for an omission that no longer applies.
  }, [options.conversationId, rejoinRun]);

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
        // effect has not run yet, then let the queue advance.
        pendingHITLRef.current = null;
        drainQueue();
      } catch (err) {
        setError(
          err instanceof Error
            ? err
            : new Error("Could not submit the approval. Please try again."),
        );
      }
    },
    [pendingHITL, drainQueue]
  );

  return {
    messages,
    input,
    handleInputChange,
    handleSubmit,
    sendMessage,
    isLoading,
    error,
    setMessages: updateMessages,
    clearMessages,
    stop,
    reload,
    pendingHITL,
    resolveHITL,
    turnVerdict,
    queuedMessages,
    cancelQueued,
    clearQueued,
    getToolCalls,
    getToolResults,
    getCitations,
    getReasoningBlocks,
  };
}