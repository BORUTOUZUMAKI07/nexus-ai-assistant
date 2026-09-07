"use client";

import React, { useState } from "react";
import {
  User,
  Bot,
  ChevronDown,
  ChevronUp,
  Copy,
  Check,
  ThumbsUp,
  ThumbsDown,
  Terminal,
  BookOpen,
  Sparkles,
  ShieldAlert,
  CheckCircle2,
  XCircle,
  MessageSquareWarning,
  RotateCw,
} from "lucide-react";

export interface CitationItem {
  filename: string;
  chunk_index: number;
  score: number;
  content_snippet: string;
}

export interface ToolCallItem {
  name: string;
  args?: unknown;
  result?: unknown;
  status: string;
}

export interface MessageItem {
  id: string;
  role: "user" | "assistant" | "system";
  content: string;
  thought_process?: string;
  model?: string;
  citations?: CitationItem[];
  tool_calls?: ToolCallItem[];
  created_at?: string;
}

export interface PendingHITLRequest {
  thread_id: string;
  request: string;
  plan?: string[];
  tool_name?: string;
}

export interface ChatAreaProps {
  messages: MessageItem[];
  isLoading: boolean;
  error?: string | null;
  onRetry?: () => void;
  pendingHITL?: PendingHITLRequest | null;
  onResolveHITL?: (
    action: "approve" | "reject" | "modify",
    data?: Record<string, unknown>
  ) => void | Promise<void>;
  onFeedback?: (
    messageId: string,
    feedback: "thumbs_up" | "thumbs_down"
  ) => void;
}

export const ChatArea: React.FC<ChatAreaProps> = ({
  messages,
  isLoading,
  error,
  onRetry,
  pendingHITL,
  onResolveHITL,
  onFeedback,
}) => {
  const [copiedId, setCopiedId] = useState<string | null>(null);
  const [expandedThoughts, setExpandedThoughts] = useState<Record<string, boolean>>({});
  const [hitlOpen, setHitlOpen] = useState(false);
  const [hitlNote, setHitlNote] = useState("");
  const [resolving, setResolving] = useState(false);
  const [resolved, setResolved] = useState<"approve" | "reject" | "modify" | null>(null);

  const handleCopy = (id: string, text: string) => {
    navigator.clipboard.writeText(text);
    setCopiedId(id);
    setTimeout(() => setCopiedId(null), 2000);
  };

  const toggleThought = (id: string) => {
    setExpandedThoughts((prev) => ({ ...prev, [id]: !prev[id] }));
  };

  const resolve = async (
    action: "approve" | "reject" | "modify",
    data?: Record<string, unknown>
  ) => {
    if (!onResolveHITL || !pendingHITL || resolving) return;
    setResolving(true);
    try {
      await onResolveHITL(action, data);
      setResolved(action);
      setHitlOpen(false);
      setHitlNote("");
    } finally {
      setResolving(false);
    }
  };

  return (
    <div className="flex-1 overflow-y-auto px-4 py-6 md:px-12 space-y-6">
      {messages.length === 0 && (
        <div className="h-full flex flex-col items-center justify-center text-center max-w-md mx-auto py-24 space-y-4">
          <div className="w-12 h-12 rounded-xl bg-[var(--bg-surface)] border border-[var(--border-subtle)] flex items-center justify-center">
            <Sparkles className="w-6 h-6 text-[var(--accent)]" />
          </div>
          <h2 className="text-xl font-semibold tracking-tight text-white">
            How can Nexus help you today?
          </h2>
          <p className="text-sm text-[var(--text-muted)] leading-relaxed">
            Research the live web, query your knowledge base, write and run
            code, and keep human approvals for sensitive actions.
          </p>
        </div>
      )}

      {/* Stream error banner */}
      {error && (
        <div className="max-w-3xl mx-auto flex items-center justify-between gap-4 rounded-xl border border-[var(--border-strong)] bg-[var(--bg-surface)] px-4 py-3 text-sm">
          <div className="flex items-center gap-3 min-w-0">
            <MessageSquareWarning className="w-4 h-4 text-[var(--status-danger)] shrink-0" />
            <span className="text-[var(--text-secondary)] truncate">
              {error}
            </span>
          </div>
          {onRetry && (
            <button
              onClick={onRetry}
              className="flex items-center gap-1.5 rounded-lg border border-[var(--border-subtle)] px-3 py-1.5 text-xs text-[var(--text-secondary)] hover:text-white hover:border-[var(--border-strong)] transition-colors whitespace-nowrap"
            >
              <RotateCw className="w-3.5 h-3.5" />
              Retry
            </button>
          )}
        </div>
      )}

      {/* HITL approval card */}
      {pendingHITL && !resolved && (
        <div className="max-w-3xl mx-auto rounded-xl border border-[var(--border-strong)] bg-[var(--bg-surface)] p-4">
          <div className="flex items-center gap-2 text-sm font-medium text-white mb-1.5">
            <ShieldAlert className="w-4 h-4 text-[var(--status-warning)]" />
            Human approval required
          </div>
          <p className="text-xs text-[var(--text-muted)] leading-relaxed mb-3">
            {pendingHITL.request}
          </p>

          {hitlOpen ? (
            <div className="space-y-2">
              <textarea
                value={hitlNote}
                onChange={(e) => setHitlNote(e.target.value)}
                rows={2}
                placeholder="Optional note, or modified instructions for the agent..."
                className="w-full resize-none rounded-lg border border-[var(--border-subtle)] bg-[var(--bg-main)] px-3 py-2 text-xs text-[var(--text-primary)] outline-none focus:border-[var(--accent)] placeholder:text-[var(--text-faint)]"
              />
              <div className="flex items-center gap-2">
                <button
                  onClick={() => resolve("modify", { note: hitlNote })}
                  disabled={resolving}
                  className="flex items-center gap-1.5 rounded-lg border border-[var(--accent)] px-3 py-1.5 text-xs text-[var(--accent-hover)] hover:bg-[var(--accent-soft)] transition-colors disabled:opacity-50"
                >
                  <CheckCircle2 className="w-3.5 h-3.5" />
                  Approve with note
                </button>
                <button
                  onClick={() => setHitlOpen(false)}
                  disabled={resolving}
                  className="rounded-lg px-3 py-1.5 text-xs text-[var(--text-muted)] hover:text-[var(--text-secondary)] transition-colors disabled:opacity-50"
                >
                  Cancel
                </button>
              </div>
            </div>
          ) : (
            <div className="flex items-center gap-2">
              <button
                onClick={() => resolve("approve")}
                disabled={resolving}
                className="flex items-center gap-1.5 rounded-lg bg-[var(--accent)] px-3 py-1.5 text-xs font-medium text-white hover:bg-[var(--accent-hover)] transition-colors disabled:opacity-50"
              >
                <CheckCircle2 className="w-3.5 h-3.5" />
                Approve
              </button>
              <button
                onClick={() => resolve("reject")}
                disabled={resolving}
                className="flex items-center gap-1.5 rounded-lg border border-[var(--border-subtle)] px-3 py-1.5 text-xs text-[var(--text-secondary)] hover:text-white hover:border-[var(--status-danger)] transition-colors disabled:opacity-50"
              >
                <XCircle className="w-3.5 h-3.5" />
                Reject
              </button>
              <button
                onClick={() => setHitlOpen(true)}
                disabled={resolving}
                className="rounded-lg px-3 py-1.5 text-xs text-[var(--text-muted)] hover:text-[var(--text-secondary)] transition-colors disabled:opacity-50"
              >
                Modify…
              </button>
            </div>
          )}
        </div>
      )}

      {messages.map((msg) => {
        const isUser = msg.role === "user";
        const hasThought = Boolean(msg.thought_process);
        const isThoughtOpen = expandedThoughts[msg.id] ?? false;

        return (
          <div
            key={msg.id}
            className={`flex gap-3.5 max-w-3xl mx-auto ${
              isUser ? "justify-end" : "justify-start"
            }`}
          >
            {/* Assistant Avatar */}
            {!isUser && (
              <div className="w-8 h-8 rounded-lg bg-[var(--bg-surface-elevated)] border border-[var(--border-subtle)] flex items-center justify-center text-white shrink-0 mt-1">
                <Bot className="w-4 h-4 text-[var(--text-secondary)]" />
              </div>
            )}

            {/* Message Bubble Body */}
            <div className={`flex flex-col space-y-2 max-w-[85%] ${isUser ? "items-end" : "items-start"}`}>
              {/* Optional Thought Process Drawer */}
              {!isUser && hasThought && (
                <div className="w-full rounded-xl overflow-hidden text-xs border border-[var(--border-subtle)] bg-[var(--bg-surface)]">
                  <button
                    onClick={() => toggleThought(msg.id)}
                    className="w-full flex items-center justify-between px-3 py-2 text-[var(--text-muted)] hover:text-[var(--text-secondary)] transition-colors"
                  >
                    <span className="flex items-center gap-1.5 font-mono text-[11px]">
                      <Sparkles className="w-3 h-3 text-[var(--accent)]" /> Thought process
                    </span>
                    {isThoughtOpen ? <ChevronUp className="w-3.5 h-3.5" /> : <ChevronDown className="w-3.5 h-3.5" />}
                  </button>
                  {isThoughtOpen && (
                    <div className="p-3 text-[var(--text-muted)] text-[11px] leading-relaxed font-mono whitespace-pre-wrap border-t border-[var(--border-subtle)]">
                      {msg.thought_process}
                    </div>
                  )}
                </div>
              )}

              {/* Main Text Bubble */}
              <div
                className={`p-4 rounded-xl text-sm leading-relaxed prose-nexus border ${
                  isUser
                    ? "bg-[var(--accent)] text-white border-[var(--accent)] rounded-tr-none"
                    : "glass-card text-[var(--text-primary)] rounded-tl-none"
                }`}
              >
                <div className="whitespace-pre-wrap">{msg.content}</div>

                {/* Inline Tool Calls Output */}
                {msg.tool_calls && msg.tool_calls.length > 0 && (
                  <div className="mt-3 space-y-2 pt-2 border-t border-[var(--border-subtle)]">
                    {msg.tool_calls.map((t, idx) => (
                      <div
                        key={idx}
                        className="rounded-lg p-2.5 text-xs border border-[var(--border-subtle)] bg-[var(--bg-main)]"
                      >
                        <div className="flex items-center justify-between text-[var(--accent)] font-mono text-[11px] mb-1">
                          <span className="flex items-center gap-1">
                            <Terminal className="w-3 h-3" /> {t.name}
                          </span>
                          <span className="text-[9px] uppercase px-1 rounded bg-[var(--bg-surface-elevated)] text-[var(--text-muted)]">
                            {t.status}
                          </span>
                        </div>
                        {t.result !== undefined && (
                          <pre className="text-[10px] text-[var(--text-secondary)] max-h-32 overflow-y-auto mt-1 bg-transparent p-0 border-0">
                            {typeof t.result === "string" ? t.result : JSON.stringify(t.result, null, 2)}
                          </pre>
                        )}
                      </div>
                    ))}
                  </div>
                )}

                {/* Grounding Footnote Citations */}
                {msg.citations && msg.citations.length > 0 && (
                  <div className="mt-3 pt-2 border-t border-[var(--border-subtle)]">
                    <div className="text-[10px] uppercase font-mono tracking-wider text-[var(--accent)] flex items-center gap-1 mb-1.5">
                      <BookOpen className="w-3 h-3" /> Grounding sources
                    </div>
                    <div className="flex flex-wrap gap-1.5">
                      {msg.citations.map((c, idx) => (
                        <div
                          key={idx}
                          title={c.content_snippet}
                          className="px-2 py-1 rounded-md border border-[var(--border-subtle)] bg-[var(--bg-main)] text-[var(--text-muted)] text-[10px] flex items-center gap-1 hover:border-[var(--border-strong)] hover:text-[var(--text-secondary)] transition-colors"
                        >
                          <span>[{idx + 1}]</span>
                          <span className="truncate max-w-[120px]">{c.filename}</span>
                        </div>
                      ))}
                    </div>
                  </div>
                )}
              </div>

              {/* Message Footer Toolbar */}
              {!isUser && (
                <div className="flex items-center gap-2 text-[var(--text-muted)] text-xs px-1">
                  <button
                    onClick={() => handleCopy(msg.id, msg.content)}
                    className="hover:text-white p-1 rounded transition-colors"
                    title="Copy response"
                  >
                    {copiedId === msg.id ? <Check className="w-3.5 h-3.5 text-[var(--status-success)]" /> : <Copy className="w-3.5 h-3.5" />}
                  </button>
                  {onFeedback && (
                    <>
                      <button
                        onClick={() => onFeedback(msg.id, "thumbs_up")}
                        className="hover:text-[var(--status-success)] p-1 rounded transition-colors"
                        title="Good response"
                      >
                        <ThumbsUp className="w-3.5 h-3.5" />
                      </button>
                      <button
                        onClick={() => onFeedback(msg.id, "thumbs_down")}
                        className="hover:text-[var(--status-danger)] p-1 rounded transition-colors"
                        title="Bad response"
                      >
                        <ThumbsDown className="w-3.5 h-3.5" />
                      </button>
                    </>
                  )}
                  {msg.model && (
                    <span className="text-[10px] text-[var(--text-faint)] font-mono ml-auto">
                      {msg.model}
                    </span>
                  )}
                </div>
              )}
            </div>

            {/* User Avatar */}
            {isUser && (
              <div className="w-8 h-8 rounded-lg bg-[var(--bg-surface-elevated)] border border-[var(--border-subtle)] flex items-center justify-center text-[var(--text-muted)] shrink-0 mt-1">
                <User className="w-4 h-4" />
              </div>
            )}
          </div>
        );
      })}

      {/* Loading Indicator */}
      {isLoading && (
        <div className="flex gap-3.5 max-w-3xl mx-auto justify-start">
          <div className="w-8 h-8 rounded-lg bg-[var(--bg-surface-elevated)] border border-[var(--border-subtle)] flex items-center justify-center shrink-0">
            <Bot className="w-4 h-4 animate-pulse text-[var(--accent)]" />
          </div>
          <div className="glass-card rounded-xl rounded-tl-none flex items-center gap-2 px-4 py-3 text-xs text-[var(--text-muted)]">
            <span className="w-2 h-2 rounded-full bg-[var(--accent)] animate-pulse" />
            <span className="font-mono">Nexus is working…</span>
          </div>
        </div>
      )}
    </div>
  );
};