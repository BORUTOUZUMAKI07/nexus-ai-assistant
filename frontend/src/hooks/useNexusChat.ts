/**
 * Nexus AI – useNexusChat hook
 * Typed streaming chat state for Nexus conversations:
 *  - streams assistant replies from the /api/chat data-stream proxy
 *  - surfaces tool calls, citations, and reasoning as typed annotations
 *  - manages human-in-the-loop (HITL) approval requests
 */
"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { sendHITLFeedback } from "@/lib/api";

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

export type NexusAnnotation =
  | ToolCallAnnotation
  | ToolResultAnnotation
  | ReasoningAnnotation
  | CitationAnnotation
  | HITLRequestAnnotation;

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
}

export interface SendMessageOptions {
  mode?: "normal" | "agent" | "code" | "research";
  conversationId?: string;
  /** base64 data URL for an image the user has attached */
  imageDataUrl?: string;
}

export function useNexusChat(options: UseNexusChatOptions = {}) {
  const [messages, setMessages] = useState<NexusMessage[]>([]);
  const [input, setInput] = useState("");
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<Error | null>(null);
  const [pendingHITL, setPendingHITL] = useState<
    HITLRequestAnnotation["data"] | null
  >(null);

  // Keep always-current copies of options, messages, and loading state for
  // async callbacks. These are updated in an effect, never during render.
  const optionsRef = useRef(options);
  const messagesRef = useRef(messages);
  const loadingRef = useRef(isLoading);
  const abortRef = useRef<AbortController | null>(null);
  // Stable ref for the conversation-created callback — avoids re-binding sendMessage.
  const onConversationCreatedRef = useRef(options.onConversationCreated);

  useEffect(() => {
    optionsRef.current = options;
    onConversationCreatedRef.current = options.onConversationCreated;
  }, [options]);

  useEffect(() => {
    messagesRef.current = messages;
  }, [messages]);

  useEffect(() => {
    loadingRef.current = isLoading;
  }, [isLoading]);

  const updateMessages = useCallback(
    (
      value:
        | NexusMessage[]
        | ((prev: NexusMessage[]) => NexusMessage[])
    ) => {
      setMessages((prev) => {
        const next =
          typeof value === "function"
            ? (value as (p: NexusMessage[]) => NexusMessage[])(prev)
            : value;
        messagesRef.current = next;
        return next;
      });
    },
    []
  );

  const handleInputChange = (
    e: React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement>
  ) => {
    setInput(e.target.value);
  };

  const sendMessage = useCallback(
    async (content: string, sendOptions?: SendMessageOptions) => {
      const trimmed = content.trim();
      const imageDataUrl = sendOptions?.imageDataUrl;
      if (!trimmed && !imageDataUrl) return;
      if (loadingRef.current) return;

      setError(null);
      setPendingHITL(null);

      const conversationId =
        sendOptions?.conversationId ?? optionsRef.current.conversationId ?? "";
      const mode = sendOptions?.mode ?? optionsRef.current.mode ?? "normal";
      const model = optionsRef.current.model;

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

      const controller = new AbortController();
      abortRef.current = controller;

      const annotations: NexusAnnotation[] = [];
      const citations: CitationAnnotation["data"][] = [];
      const toolCalls: ToolCallAnnotation["data"][] = [];
      let reasoningBuffer = "";

      const patchAssistant = (content: string, annos: NexusAnnotation[]) => {
        updateMessages((prev) =>
          prev.map((m) =>
            m.id === assistantMsgId
              ? { ...m, content, annotations: annos.length > 0 ? annos : m.annotations }
              : m
          )
        );
      };

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

        const response = await fetch("/api/chat", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          signal: controller.signal,
          body: JSON.stringify({
            messages: backendMessages,
            conversationId,
            mode,
          }),
        });

        if (!response.ok || !response.body) {
          throw new Error(`Chat API error: ${response.statusText}`);
        }

        const reader = response.body.getReader();
        const decoder = new TextDecoder();
        let streamContent = "";
        let streamError: string | null = null;
        // Track whether the backend assigned a new conversation id so we can
        // surface it to the page via onConversationCreated.
        let resolvedConversationId: string | null = null;

        // SSE lines can be split across network chunks (and JSON payloads may
        // even contain literal newlines), so any trailing partial line is
        // carried into the next iteration instead of being parsed eagerly.
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
            patchAssistant(streamContent, annotations);
            return;
          }

          // Annotations
          if (!line.startsWith("8:")) return;
          try {
            const parsed: unknown[] = JSON.parse(line.slice(2));
            if (!Array.isArray(parsed)) return;
            for (const ann of parsed) {
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
                setPendingHITL(typed.data);
                annotations.push(typed);
              } else if (
                (ann as { type: string; data?: { thread_id?: string } }).type === "conversation_created"
              ) {
                // Backend resolved a new conversation UUID — capture it for the callback.
                const newId = (ann as { type: string; data: { thread_id: string } }).data?.thread_id;
                if (newId) resolvedConversationId = newId;
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
            patchAssistant(streamContent, next);
          } catch {
            // Ignore malformed annotation payloads
          }
        };

        while (true) {
          const { done, value } = await reader.read();
          if (done) break;

          const chunk = decoder.decode(value, { stream: true });
          buffer += chunk;
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
        patchAssistant(streamContent, finalAnnotations);
        // Fire the conversation-created callback if the backend assigned a new id.
        if (resolvedConversationId && !conversationId && onConversationCreatedRef.current) {
          onConversationCreatedRef.current(resolvedConversationId);
        }
        if (streamError) {
          setError(new Error(streamError));
          return;
        }
      } catch (err) {
        if (err instanceof Error && err.name === "AbortError") return;
        setError(err instanceof Error ? err : new Error("Chat stream failed"));
      } finally {
        setIsLoading(false);
        abortRef.current = null;
      }
    },
    [updateMessages]
  );

  const handleSubmit = (e?: React.FormEvent) => {
    if (e) e.preventDefault();
    const value = input.trim();
    if (!value) return;
    setInput("");
    void sendMessage(value);
  };

  const stop = useCallback(() => {
    abortRef.current?.abort();
    abortRef.current = null;
    setIsLoading(false);
  }, []);

  const reload = useCallback(async () => {
    // A reload must supersede whatever stream (if any) is still running. Abort
    // it and reset the loading ref synchronously so the follow-up send is not
    // swallowed by the in-flight guard.
    abortRef.current?.abort();
    abortRef.current = null;
    loadingRef.current = false;
    setIsLoading(false);
    setError(null);

    const msgs = messagesRef.current;
    const lastUser = [...msgs].reverse().find((m) => m.role === "user");
    if (!lastUser) return;
    const idx = msgs.findIndex((m) => m.id === lastUser.id);
    updateMessages(() => msgs.slice(0, idx + 1));
    await sendMessage(lastUser.content);
  }, [sendMessage, updateMessages]);

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
    setPendingHITL(null);
    setError(null);
  }, [updateMessages]);

  // HITL approval/rejection
  const resolveHITL = useCallback(
    async (
      action: "approve" | "reject" | "modify",
      data?: Record<string, unknown>
    ) => {
      const request = pendingHITL;
      if (!request) return;
      try {
        await sendHITLFeedback({
          threadId: request.thread_id,
          action,
          data: data ?? (action === "modify" ? { approved: true } : {}),
        });
      } finally {
        setPendingHITL(null);
      }
    },
    [pendingHITL]
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
    getToolCalls,
    getToolResults,
    getCitations,
    getReasoningBlocks,
  };
}