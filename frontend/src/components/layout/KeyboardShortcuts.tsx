"use client";

import * as React from "react";
import { HelpCircle, X, Keyboard, Command, Code } from "lucide-react";

const SHORTCUTS = [
  {
    group: "Navigation",
    items: [
      { key: "Cmd/Ctrl + K", label: "Open command palette", desc: "Quick actions and search" },
      { key: "Cmd/Ctrl + Enter", label: "Send message", desc: "Submit your prompt" },
      { key: "Esc", label: "Cancel generation", desc: "Stop an in-progress run" },
    ],
  },
  {
    group: "Slash Commands",
    items: [
      { key: "/image", label: "Generate Image", desc: "Create an image from a prompt (free)" },
      { key: "/code", label: "Code Subagent", desc: "Activate Python sandbox & code agent" },
      { key: "/web", label: "Web Search", desc: "Enable live web search (CRAG)" },
      { key: "/deep", label: "Deep Agent", desc: "LangGraph Tree-of-Thought reasoning" },
      { key: "/doc", label: "Attach Document", desc: "Open document picker" },
      { key: "/review", label: "Code Review", desc: "Senior-level code analysis" },
      { key: "/research", label: "Research Brief", desc: "Structured multi-perspective analysis" },
    ],
  },
  {
    group: "Accessibility",
    items: [
      { key: "Cmd/Ctrl + Shift + D", label: "Toggle dark mode", desc: "Switch between light and dark" },
      { key: "?", label: "Show this help", desc: "Display keyboard shortcuts" },
    ],
  },
];

export interface KeyboardShortcutsProps {
  open: boolean;
  onClose: () => void;
}

export function KeyboardShortcutsGuide({ open, onClose }: KeyboardShortcutsProps): React.ReactElement | null {
  if (!open) return null;

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center"
      role="dialog"
      aria-modal="true"
      aria-label="Keyboard shortcuts"
    >
      <div
        className="fixed inset-0 bg-black/40 backdrop-blur-sm"
        onClick={onClose}
      />
      <div
        className="w-full max-w-2xl p-6 bg-[var(--bg-surface)] border border-[var(--border-subtle)] rounded-xl shadow-2xl animate-in fade-in zoom-in-95 duration-200"
      >
        <div className="flex items-center justify-between mb-6">
          <h2 className="text-lg font-semibold text-[var(--text-primary)] flex items-center gap-2">
            <Keyboard className="w-5 h-5 text-[var(--accent)]" /> Keyboard Shortcuts
          </h2>
          <button
            onClick={onClose}
            className="rounded-md p-2 text-[var(--text-secondary)] hover:text-[var(--text-primary)] hover:bg-[var(--bg-surface)]"
            aria-label="Close shortcuts"
          >
            <X className="w-5 h-5" />
          </button>
        </div>
        <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
          {SHORTCUTS.map((group) => (
            <div key={group.group} className="space-y-3">
              <h3 className="text-xs font-medium uppercase tracking-wider text-[var(--text-muted)] mb-3 border-b border-[var(--border-subtle)] pb-1">
                {group.group}
              </h3>
              {group.items.map((item) => (
                <div
                  key={item.key}
                  className="flex items-start justify-between gap-3 p-2.5 rounded-lg border border-[var(--border-subtle)] bg-[var(--bg-main)] hover:border-[var(--accent)]/50 transition-colors"
                >
                  <div className="flex items-center gap-2 shrink-0">
                    <Command className="w-3.5 h-3.5 text-[var(--text-muted)]" />
                    <kbd className="px-1.5 py-0.5 rounded text-[10px] font-mono font-medium border border-[var(--border-subtle)] bg-[var(--bg-surface)]">
                      {item.key}
                    </kbd>
                  </div>
                  <div className="flex-1 min-w-0">
                    <div className="text-sm font-medium text-[var(--text-primary)] leading-tight">
                      {item.label}
                    </div>
                    <div className="text-[10px] text-[var(--text-muted)] mt-0.5">
                      {item.desc}
                    </div>
                  </div>
                </div>
              ))}
            </div>
          ))}
        </div>
        <p className="mt-6 text-[10px] text-[var(--text-muted)] text-center">
          Press <kbd className="px-1 py-0.5 rounded text-[9px] border border-[var(--border-subtle)] bg-[var(--bg-surface)]">?</kbd> again to close.
        </p>
      </div>
    </div>
  );
}
