"use client";

import { FileCode } from "lucide-react";
import type { ArtifactItem } from "@/components/ArtifactCanvas";

/**
 * The way back into artifacts saved in earlier sessions.
 *
 * The canvas could always be opened from a code block in a message, but those
 * are transient items that exist only for the current page session. A saved
 * artifact had no entry point at all: the canvas's own multi-file tab bar only
 * renders when more than one artifact is open, and it is only reachable once
 * something has already opened the canvas. So with a single saved artifact —
 * the common case — the file was stored, versioned on the server, and
 * completely unreachable. Loading the list is therefore not enough on its own;
 * this is the control that makes it usable.
 *
 * Mirrors PlanHistory: a collapsed <details> strip, because this is reference
 * information rather than the primary conversation.
 */
export default function SavedArtifacts({
  artifacts,
  onOpen,
}: {
  artifacts: ArtifactItem[];
  onOpen: (artifact: ArtifactItem) => void;
}) {
  if (artifacts.length === 0) return null;

  return (
    <details className="mx-4 mb-2 rounded-lg border border-[var(--border-subtle)] bg-[var(--bg-surface)]">
      <summary className="flex cursor-pointer items-center gap-2 px-3 py-2 text-[11px] font-semibold uppercase tracking-wider text-[var(--text-muted)] select-none hover:text-[var(--text-secondary)]">
        <span className="text-[var(--accent)]">Saved artifacts</span>
        <span className="rounded-full border border-[var(--border-subtle)] px-1.5 text-[10px] normal-case tracking-normal text-[var(--text-secondary)]">
          {artifacts.length}
        </span>
      </summary>

      <ul className="max-h-56 overflow-y-auto border-t border-[var(--border-subtle)]">
        {artifacts.map((artifact) => (
          <li key={artifact.id} className="border-b border-[var(--border-subtle)] last:border-b-0">
            <button
              type="button"
              onClick={() => onOpen(artifact)}
              className="flex w-full items-center gap-2.5 px-3 py-2 text-left hover:bg-[var(--bg-surface-tint)]"
            >
              <FileCode className="w-3.5 h-3.5 shrink-0 text-[var(--accent)]" />
              <span className="min-w-0 flex-1">
                <span className="block truncate text-[12px] text-[var(--text-primary)]">
                  {artifact.title}
                </span>
                <span className="block text-[10px] text-[var(--text-faint)]">
                  {artifact.language}
                  {artifact.version !== undefined && ` · v${artifact.version}`}
                </span>
              </span>
            </button>
          </li>
        ))}
      </ul>
    </details>
  );
}
