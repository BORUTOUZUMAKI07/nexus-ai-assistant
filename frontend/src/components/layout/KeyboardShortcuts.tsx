"use client";

import * as React from "react";
import { X, Keyboard, Command } from "lucide-react";
import {
  Dialog,
  DialogClose,
  DialogContent,
  DialogDescription,
  DialogTitle,
} from "@/components/ui/dialog";

const SHORTCUTS = [
  {
    group: "Navigation",
    items: [
      { key: "Cmd/Ctrl + K", label: "Open command palette", desc: "Quick actions and search" },
      { key: "Cmd/Ctrl + Enter", label: "Send message", desc: "Submit the composer draft" },
      { key: "Esc", label: "Stop generation", desc: "Cancel an in-progress run" },
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

export function KeyboardShortcutsGuide({ open, onClose }: KeyboardShortcutsProps): React.ReactElement {
  return (
    <Dialog
      open={open}
      onOpenChange={(nextOpen) => {
        if (!nextOpen) onClose();
      }}
    >
      <DialogContent className="nx-dialog max-w-2xl p-6" aria-describedby="shortcuts-description">
        <DialogDescription id="shortcuts-description" className="sr-only">
          A list of the keyboard shortcuts available in Nexus.
        </DialogDescription>
        <div className="flex items-center justify-between mb-6">
          <DialogTitle className="text-lg font-semibold text-[var(--text-primary)] flex items-center gap-2">
            <Keyboard className="w-5 h-5 text-[var(--accent)]" aria-hidden="true" /> Keyboard Shortcuts
          </DialogTitle>
          <DialogClose asChild>
            <button
              type="button"
              className="rounded-md p-2 text-[var(--text-secondary)] hover:text-[var(--text-primary)] hover:bg-[var(--bg-surface)]"
              aria-label="Close shortcuts"
            >
              <X className="w-5 h-5" aria-hidden="true" />
            </button>
          </DialogClose>
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
                    <Command className="w-3.5 h-3.5 text-[var(--text-muted)]" aria-hidden="true" />
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
          Press <kbd className="px-1 py-0.5 rounded text-[9px] border border-[var(--border-subtle)] bg-[var(--bg-surface)]">?</kbd> again or Esc to close.
        </p>
      </DialogContent>
    </Dialog>
  );
}
