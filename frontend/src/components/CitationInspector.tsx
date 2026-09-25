"use client";

import React from "react";
import { X, BookOpen, ExternalLink, Sparkles, Copy, Check } from "lucide-react";
import { CitationItem } from "./ChatArea";

export interface CitationInspectorProps {
  citation: CitationItem | null;
  onClose: () => void;
}

export const CitationInspector: React.FC<CitationInspectorProps> = ({
  citation,
  onClose,
}) => {
  const [copied, setCopied] = React.useState(false);

  if (!citation) return null;

  const matchPercent = Math.min(100, Math.max(1, Math.round(citation.score * 100)));

  const handleCopy = () => {
    navigator.clipboard.writeText(citation.content_snippet);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  return (
    <div className="fixed inset-y-0 right-0 w-full sm:w-96 z-40 bg-[var(--bg-surface)] border-l border-[var(--border-subtle)] shadow-2xl flex flex-col transition-transform duration-200">
      {/* Header */}
      <div className="flex items-center justify-between px-4 py-3 border-b border-[var(--border-subtle)] bg-[var(--bg-surface-elevated)]">
        <div className="flex items-center gap-2 min-w-0">
          <BookOpen className="w-4 h-4 text-[var(--accent)] shrink-0" />
          <h3 className="text-xs font-semibold text-white truncate">
            Grounding Source
          </h3>
        </div>
        <button
          onClick={onClose}
          className="p-1.5 rounded-lg text-[var(--text-muted)] hover:text-white hover:bg-[var(--bg-main)] transition-colors"
          title="Close Inspector"
        >
          <X className="w-4 h-4" />
        </button>
      </div>

      {/* Content */}
      <div className="flex-1 overflow-y-auto p-4 space-y-4 text-xs">
        {/* Document Info Card */}
        <div className="p-3 rounded-xl border border-[var(--border-subtle)] bg-[var(--bg-main)] space-y-2">
          <div className="flex items-center justify-between">
            <span className="text-[10px] uppercase font-mono tracking-wider text-[var(--text-muted)]">
              Document File
            </span>
            <span className="flex items-center gap-1 text-[10px] font-mono px-2 py-0.5 rounded-full bg-[var(--accent-soft)] text-[var(--accent)] font-semibold border border-[var(--accent)]/20">
              <Sparkles className="w-2.5 h-2.5" />
              {matchPercent}% Match
            </span>
          </div>
          <p className="font-medium text-white text-sm break-all">
            {citation.filename}
          </p>
          <div className="text-[10px] font-mono text-[var(--text-muted)]">
            Chunk Index #{citation.chunk_index} • Vector RRF Grounded
          </div>
        </div>

        {/* Verbatim Passage Box */}
        <div className="space-y-1.5">
          <div className="flex items-center justify-between text-[11px] text-[var(--text-secondary)] font-medium">
            <span>Verbatim Extracted Chunk</span>
            <button
              onClick={handleCopy}
              className="flex items-center gap-1 text-[10px] text-[var(--text-muted)] hover:text-white transition-colors"
            >
              {copied ? (
                <>
                  <Check className="w-3 h-3 text-[var(--status-success)]" />
                  Copied
                </>
              ) : (
                <>
                  <Copy className="w-3 h-3" />
                  Copy Snippet
                </>
              )}
            </button>
          </div>
          <div className="p-3 rounded-xl border border-[var(--border-subtle)] bg-[var(--bg-main)] text-[var(--text-secondary)] leading-relaxed font-sans text-xs whitespace-pre-wrap select-text">
            {citation.content_snippet}
          </div>
        </div>

        {/* Info Box */}
        <div className="p-3 rounded-lg border border-[var(--border-subtle)] bg-[var(--bg-surface-elevated)]/50 text-[11px] text-[var(--text-muted)] leading-normal flex items-start gap-2">
          <ExternalLink className="w-3.5 h-3.5 text-[var(--accent)] shrink-0 mt-0.5" />
          <span>
            This chunk was semantically retrieved from Qdrant vector storage and verified by the LangGraph Evidence Gate to eliminate hallucinations.
          </span>
        </div>
      </div>
    </div>
  );
};
