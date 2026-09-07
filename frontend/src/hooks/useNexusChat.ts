/**
 * Nexus AI – useNexusChat hook
 * Provides typed state management for Nexus conversational workflows and HITL feedback.
 */
"use client";

import { useCallback, useState } from "react";
import { sendHITLFeedback } from "@/lib/api";

export interface ToolCallAnnotation {
  type: "tool_call";
  data: {
    tool_name: string;
    tool_input: Record<string, unknown>;
    tool_call_id: string;
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
    source: string;
    url?: string;
    snippet: string;
    score: number;
  };
}

export interface HITLRequestAnnotation {
  type: "hitl_request";
  data: {
    thread_id: string;
    request: string;
    plan?: string[];
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
  annotations?: NexusAnnotation[];
}

interface UseNexusChatOptions {
  conversationId?: string;
  userId?: string;
  mode?: "normal" | "agent" | "code" | "research";
}

export function useNexusChat({}: UseNexusChatOptions = {}) {
  const [messages, setMessages] = useState<NexusMessage[]>([]);
  const [input, setInput] = useState("");
  const [isLoading, setIsLoading] = useState(false);
  const [error] = useState<Error | null>(null);
  const [pendingHITL, setPendingHITL] = useState<
    HITLRequestAnnotation["data"] | null
  >(null);

  const handleInputChange = (e: React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement>) => {
    setInput(e.target.value);
  };

  const handleSubmit = (e?: React.FormEvent) => {
    if (e) e.preventDefault();
  };

  const stop = () => {
    setIsLoading(false);
  };

  const reload = () => {
    // optional reload handler
  };

  // Extract typed annotations from messages
  const getAnnotations = useCallback(
    (message: NexusMessage): NexusAnnotation[] =>
      message.annotations ?? [],
    []
  );

  const getToolCalls = useCallback(
    (message: NexusMessage) =>
      getAnnotations(message).filter(
        (a): a is ToolCallAnnotation => a.type === "tool_call"
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

  // HITL approval/rejection
  const resolveHITL = useCallback(
    async (action: "approve" | "reject", data?: Record<string, unknown>) => {
      if (!pendingHITL) return;
      await sendHITLFeedback({
        threadId: pendingHITL.thread_id,
        action,
        data,
      });
      setPendingHITL(null);
    },
    [pendingHITL]
  );

  return {
    messages,
    input,
    handleInputChange,
    handleSubmit,
    isLoading,
    error,
    setMessages,
    stop,
    reload,
    pendingHITL,
    resolveHITL,
    getToolCalls,
    getCitations,
    getReasoningBlocks,
  };
}
