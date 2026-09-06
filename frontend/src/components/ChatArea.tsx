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
  Search,
  ExternalLink,
  BookOpen,
  Sparkles,
} from "lucide-react";

export interface CitationItem {
  filename: string;
  chunk_index: number;
  score: number;
  content_snippet: string;
}

export interface ToolCallItem {
  name: string;
  args?: any;
  result?: any;
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

interface ChatAreaProps {
  messages: MessageItem[];
  isLoading: boolean;
  onFeedback?: (messageId: string, feedback: "thumbs_up" | "thumbs_down") => void;
}

export const ChatArea: React.FC<ChatAreaProps> = ({ messages, isLoading, onFeedback }) => {
  const [copiedId, setCopiedId] = useState<string | null>(null);
  const [expandedThoughts, setExpandedThoughts] = useState<Record<string, boolean>>({});

  const handleCopy = (id: string, text: string) => {
    navigator.clipboard.writeText(text);
    setCopiedId(id);
    setTimeout(() => setCopiedId(null), 2000);
  };

  const toggleThought = (id: string) => {
    setExpandedThoughts((prev) => ({ ...prev, [id]: !prev[id] }));
  };

  return (
    <div className="flex-1 overflow-y-auto px-4 py-6 md:px-12 space-y-6">
      {messages.length === 0 && (
        <div className="h-full flex flex-col items-center justify-center text-center max-w-md mx-auto py-24 space-y-4">
          <div className="w-12 h-12 rounded-2xl bg-gradient-to-tr from-violet-600 via-indigo-600 to-cyan-400 flex items-center justify-center shadow-xl shadow-violet-600/30">
            <Sparkles className="w-6 h-6 text-white" />
          </div>
          <h2 className="text-xl font-semibold text-white">How can Nexus help you today?</h2>
          <p className="text-xs text-neutral-400 leading-relaxed">
            Equipped with Groq ultra-low latency inference, Qdrant Hybrid RAG, E2B Python sandbox, and Firecrawl live web intelligence.
          </p>
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
              <div className="w-8 h-8 rounded-lg bg-gradient-to-br from-violet-600 to-indigo-700 flex items-center justify-center text-white shrink-0 shadow-md shadow-violet-600/20 mt-1">
                <Bot className="w-4 h-4" />
              </div>
            )}

            {/* Message Bubble Body */}
            <div className={`flex flex-col space-y-2 max-w-[85%] ${isUser ? "items-end" : "items-start"}`}>
              {/* Optional Thought Process Drawer (DeepSeek R1 reasoning) */}
              {!isUser && hasThought && (
                <div className="w-full bg-[#0d101a] border border-violet-500/20 rounded-xl overflow-hidden text-xs">
                  <button
                    onClick={() => toggleThought(msg.id)}
                    className="w-full flex items-center justify-between px-3 py-2 text-violet-300/80 hover:text-violet-200 transition-colors bg-violet-950/20"
                  >
                    <span className="flex items-center gap-1.5 font-mono text-[11px]">
                      <Sparkles className="w-3 h-3 text-violet-400" /> Thought Process
                    </span>
                    {isThoughtOpen ? <ChevronUp className="w-3.5 h-3.5" /> : <ChevronDown className="w-3.5 h-3.5" />}
                  </button>
                  {isThoughtOpen && (
                    <div className="p-3 text-neutral-400 text-[11px] leading-relaxed font-mono whitespace-pre-wrap border-t border-violet-500/10">
                      {msg.thought_process}
                    </div>
                  )}
                </div>
              )}

              {/* Main Text Bubble */}
              <div
                className={`p-4 rounded-2xl text-sm leading-relaxed prose-nexus ${
                  isUser
                    ? "bg-violet-600 text-white rounded-tr-none shadow-lg shadow-violet-600/15"
                    : "glass-card text-neutral-100 rounded-tl-none"
                }`}
              >
                <div className="whitespace-pre-wrap">{msg.content}</div>

                {/* Inline Tool Calls Output (if any) */}
                {msg.tool_calls && msg.tool_calls.length > 0 && (
                  <div className="mt-3 space-y-2 pt-2 border-t border-[var(--border-subtle)]">
                    {msg.tool_calls.map((t, idx) => (
                      <div
                        key={idx}
                        className="bg-[#0b0e17] border border-[var(--border-subtle)] rounded-lg p-2.5 text-xs"
                      >
                        <div className="flex items-center justify-between text-cyan-400 font-mono text-[11px] mb-1">
                          <span className="flex items-center gap-1">
                            <Terminal className="w-3 h-3" /> {t.name}
                          </span>
                          <span className="text-[9px] uppercase px-1 rounded bg-cyan-950 text-cyan-300">
                            {t.status}
                          </span>
                        </div>
                        {t.result && (
                          <pre className="text-[10px] text-neutral-300 max-h-32 overflow-y-auto mt-1 bg-transparent p-0 border-0">
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
                    <div className="text-[10px] uppercase font-mono tracking-wider text-cyan-400 flex items-center gap-1 mb-1.5">
                      <BookOpen className="w-3 h-3" /> Grounding Sources
                    </div>
                    <div className="flex flex-wrap gap-1.5">
                      {msg.citations.map((c, idx) => (
                        <div
                          key={idx}
                          title={c.content_snippet}
                          className="px-2 py-1 rounded bg-[#090b12] border border-cyan-500/20 text-cyan-300 text-[10px] flex items-center gap-1 hover:border-cyan-500/40 transition-colors"
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
                <div className="flex items-center gap-2 text-neutral-400 text-xs px-1">
                  <button
                    onClick={() => handleCopy(msg.id, msg.content)}
                    className="hover:text-white p-1 rounded transition-colors"
                    title="Copy response"
                  >
                    {copiedId === msg.id ? <Check className="w-3.5 h-3.5 text-emerald-400" /> : <Copy className="w-3.5 h-3.5" />}
                  </button>
                  {onFeedback && (
                    <>
                      <button
                        onClick={() => onFeedback(msg.id, "thumbs_up")}
                        className="hover:text-emerald-400 p-1 rounded transition-colors"
                        title="Good response"
                      >
                        <ThumbsUp className="w-3.5 h-3.5" />
                      </button>
                      <button
                        onClick={() => onFeedback(msg.id, "thumbs_down")}
                        className="hover:text-rose-400 p-1 rounded transition-colors"
                        title="Bad response"
                      >
                        <ThumbsDown className="w-3.5 h-3.5" />
                      </button>
                    </>
                  )}
                  {msg.model && (
                    <span className="text-[10px] text-neutral-400 font-mono ml-auto">
                      {msg.model}
                    </span>
                  )}
                </div>
              )}
            </div>

            {/* User Avatar */}
            {isUser && (
              <div className="w-8 h-8 rounded-lg bg-neutral-800 flex items-center justify-center text-neutral-300 shrink-0 mt-1">
                <User className="w-4 h-4" />
              </div>
            )}
          </div>
        );
      })}

      {/* Loading Indicator */}
      {isLoading && (
        <div className="flex gap-3.5 max-w-3xl mx-auto justify-start">
          <div className="w-8 h-8 rounded-lg bg-gradient-to-br from-violet-600 to-indigo-700 flex items-center justify-center text-white shrink-0 shadow-md shadow-violet-600/20">
            <Bot className="w-4 h-4 animate-spin" />
          </div>
          <div className="glass-card p-3 rounded-2xl rounded-tl-none flex items-center gap-2 text-xs text-neutral-400">
            <span className="w-2 h-2 rounded-full bg-violet-500 animate-pulse" />
            <span className="font-mono">Nexus is reasoning...</span>
          </div>
        </div>
      )}
    </div>
  );
};
