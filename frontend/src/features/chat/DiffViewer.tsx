"use client";

import * as React from "react";
import { cn } from "@/lib/utils";

/**
 * Inline diff between two text versions.
 *
 * Lines are compared naively (line-by-line, no LCS): a line in `a` that is
 * not in `b` is removed; a line in `b` not in `a` is added; everything else is
 * context. Not a true edit script — moved lines show as remove+add — but it is
 * cheap, dependency-free, and accurate enough for an artifact version diff.
 */
export function DiffViewer({
  oldText,
  newText,
}: {
  oldText: string;
  newText: string;
}): React.ReactElement {
  const oldLines = oldText.split("\n");
  const newLines = newText.split("\n");

  // Build a multiset of new lines so we can match each old line to at most one
  // identical new line. Duplicate lines consume one match each.
  const newCounts = new Map<string, number>();
  for (const line of newLines) {
    newCounts.set(line, (newCounts.get(line) ?? 0) + 1);
  }

  type DiffLine = { type: "add" | "remove" | "context"; content: string };
  const diff: DiffLine[] = [];

  for (const line of oldLines) {
    const remaining = newCounts.get(line) ?? 0;
    if (remaining > 0) {
      newCounts.set(line, remaining - 1);
      diff.push({ type: "context", content: line });
    } else {
      diff.push({ type: "remove", content: line });
    }
  }

  // Any new lines not matched to an old line are additions, in their original order.
  const usedOld = new Map<string, number>();
  for (const line of oldLines) {
    usedOld.set(line, (usedOld.get(line) ?? 0) + 1);
  }
  for (const line of newLines) {
    const oldRemaining = usedOld.get(line) ?? 0;
    if (oldRemaining <= 0) {
      diff.push({ type: "add", content: line });
    } else {
      usedOld.set(line, oldRemaining - 1);
    }
  }

  const rowClass = (type: DiffLine["type"]) =>
    cn(
      "px-2 py-0.5 text-xs font-mono text-[11px] border-b border-[var(--border-subtle)] last:border-b-0 selectable",
      {
        "bg-[var(--status-danger)]/20 text-[var(--status-danger)]": type === "remove",
        "bg-[var(--status-success)]/20 text-[var(--status-success)]": type === "add",
        "bg-[var(--bg-main)] text-[var(--text-secondary)]": type === "context",
      },
    );

  const gutterIcon = (type: DiffLine["type"]) => {
    if (type === "remove") return "-";
    if (type === "add") return "+";
    return " ";
  };

  const addedCount = diff.filter((d) => d.type === "add").length;
  const removedCount = diff.filter((d) => d.type === "remove").length;

  return (
    <div className="flex flex-col h-full min-h-0">
      <div className="flex items-center justify-between px-3 py-1.5 border-b border-[var(--border-subtle)] bg-[var(--bg-surface-elevated)] text-[10px] font-semibold uppercase tracking-wider text-[var(--text-muted)]">
        <span className="flex items-center gap-1">
          <span className={`w-4 shrink-0 ${removedCount > 0 ? "text-[var(--status-danger)]" : "text-[var(--text-faint)]"}`}>
            −{removedCount}
          </span>
          <span className={`w-4 shrink-0 ${addedCount > 0 ? "text-[var(--status-success)]" : "text-[var(--text-faint)]"}`}>
            +{addedCount}
          </span>
        </span>
        <span className="flex-1 truncate">Diff</span>
      </div>
      <div className="flex-1 overflow-auto min-h-0">
        <table className="w-full text-left border-collapse">
          <tbody>
            {diff.map((d, i) => (
              <tr key={i} className={rowClass(d.type)}>
                <td className="w-4 shrink-0 p-1 text-[var(--text-faint)]">
                  {gutterIcon(d.type)}
                </td>
                <td className="p-1 truncate whitespace-pre break-words font-mono text-[11px]">
                  {d.content}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
