"use client";

import * as React from "react";
import { cn } from "@/lib/utils";

// ─── Myers Diff Algorithm ────────────────────────────────────────────────────
// True O(ND) shortest edit script (SES) implementation. Produces the minimal
// set of insertions and deletions to transform `a` into `b`, unlike the naive
// multiset approach which loses ordering information and shows moved lines as
// spurious add+remove pairs.

type DiffOp = "equal" | "insert" | "delete";
type DiffEntry = { op: DiffOp; line: string };

function myersDiff(a: string[], b: string[]): DiffEntry[] {
  const n = a.length;
  const m = b.length;
  const max = n + m;

  // Fast path: identical content
  if (n === m && a.every((line, i) => line === b[i])) {
    return a.map((line) => ({ op: "equal" as const, line }));
  }

  // Fast path: one side empty
  if (n === 0) return b.map((line) => ({ op: "insert" as const, line }));
  if (m === 0) return a.map((line) => ({ op: "delete" as const, line }));

  // V stores the furthest-reaching endpoints for each diagonal k.
  // We keep a history of V at each step d for backtracking.
  const vHistory: Map<number, number>[] = [];
  const v = new Map<number, number>();
  v.set(1, 0);

  let found = false;
  for (let d = 0; d <= max; d++) {
    const vSnap = new Map(v);
    vHistory.push(vSnap);

    for (let k = -d; k <= d; k += 2) {
      let x: number;
      if (k === -d || (k !== d && (v.get(k - 1) ?? 0) < (v.get(k + 1) ?? 0))) {
        x = v.get(k + 1) ?? 0; // move down (insert)
      } else {
        x = (v.get(k - 1) ?? 0) + 1; // move right (delete)
      }
      let y = x - k;

      // Follow the diagonal (equal lines)
      while (x < n && y < m && a[x] === b[y]) {
        x++;
        y++;
      }

      v.set(k, x);

      if (x >= n && y >= m) {
        found = true;
        break;
      }
    }
    if (found) break;
  }

  // Backtrack to recover the edit script
  const result: DiffEntry[] = [];
  let x = n;
  let y = m;

  for (let d = vHistory.length - 1; d > 0; d--) {
    const k = x - y;
    const vPrev = vHistory[d - 1];

    let prevK: number;
    if (k === -d || (k !== d && (vPrev.get(k - 1) ?? 0) < (vPrev.get(k + 1) ?? 0))) {
      prevK = k + 1; // came from an insert
    } else {
      prevK = k - 1; // came from a delete
    }

    const prevX = vPrev.get(prevK) ?? 0;
    const prevY = prevX - prevK;

    // Diagonal (equal) moves
    while (x > prevX && y > prevY) {
      x--;
      y--;
      result.push({ op: "equal", line: a[x] });
    }

    if (d > 0) {
      if (x === prevX) {
        // Insert
        y--;
        result.push({ op: "insert", line: b[y] });
      } else {
        // Delete
        x--;
        result.push({ op: "delete", line: a[x] });
      }
    }
  }

  // Remaining diagonal at d=0
  while (x > 0 && y > 0) {
    x--;
    y--;
    result.push({ op: "equal", line: a[x] });
  }

  result.reverse();
  return result;
}

// ─── Character-level intra-line diff ─────────────────────────────────────────
// When a delete is immediately followed by an insert (i.e. a change), we
// compute a character-level diff to highlight exactly what changed within
// the line, giving the Cursor-style green/red inline character highlights.

function charDiff(
  oldLine: string,
  newLine: string,
): { old: { text: string; hi: boolean }[]; new: { text: string; hi: boolean }[] } {
  // Simple LCS on characters for intra-line highlighting
  const a = oldLine;
  const b = newLine;
  const n = a.length;
  const m = b.length;

  // Build LCS length table (space-optimized)
  const prev = new Uint16Array(m + 1);
  const curr = new Uint16Array(m + 1);
  const dir: Uint8Array[] = [];
  for (let i = 0; i <= n; i++) dir.push(new Uint8Array(m + 1));

  for (let i = 1; i <= n; i++) {
    for (let j = 1; j <= m; j++) {
      if (a[i - 1] === b[j - 1]) {
        curr[j] = prev[j - 1] + 1;
        dir[i][j] = 1; // diagonal
      } else if (prev[j] >= curr[j - 1]) {
        curr[j] = prev[j];
        dir[i][j] = 2; // up
      } else {
        curr[j] = curr[j - 1];
        dir[i][j] = 3; // left
      }
    }
    prev.set(curr);
  }

  // Backtrack
  const oldParts: { text: string; hi: boolean }[] = [];
  const newParts: { text: string; hi: boolean }[] = [];
  let i = n;
  let j = m;
  let oldBuf = "";
  let newBuf = "";
  let eqBuf = "";

  const flushChange = () => {
    if (eqBuf) {
      oldParts.push({ text: eqBuf, hi: false });
      newParts.push({ text: eqBuf, hi: false });
      eqBuf = "";
    }
    if (oldBuf) {
      oldParts.push({ text: oldBuf, hi: true });
      oldBuf = "";
    }
    if (newBuf) {
      newParts.push({ text: newBuf, hi: true });
      newBuf = "";
    }
  };

  const chars: Array<[number, string]> = []; // 0=eq, 1=del, 2=ins
  while (i > 0 || j > 0) {
    if (i > 0 && j > 0 && dir[i][j] === 1) {
      chars.push([0, a[i - 1]]);
      i--;
      j--;
    } else if (i > 0 && (j === 0 || dir[i][j] === 2)) {
      chars.push([1, a[i - 1]]);
      i--;
    } else {
      chars.push([2, b[j - 1]]);
      j--;
    }
  }
  chars.reverse();

  for (const [type, ch] of chars) {
    if (type === 0) {
      if (oldBuf || newBuf) flushChange();
      eqBuf += ch;
    } else if (type === 1) {
      if (eqBuf) {
        oldParts.push({ text: eqBuf, hi: false });
        newParts.push({ text: eqBuf, hi: false });
        eqBuf = "";
      }
      oldBuf += ch;
    } else {
      if (eqBuf) {
        oldParts.push({ text: eqBuf, hi: false });
        newParts.push({ text: eqBuf, hi: false });
        eqBuf = "";
      }
      newBuf += ch;
    }
  }
  flushChange();

  return { old: oldParts, new: newParts };
}

// ─── DiffViewer Component ────────────────────────────────────────────────────

export function DiffViewer({
  oldText,
  newText,
}: {
  oldText: string;
  newText: string;
}): React.ReactElement {
  const oldLines = React.useMemo(() => oldText.split("\n"), [oldText]);
  const newLines = React.useMemo(() => newText.split("\n"), [newText]);

  const rawDiff = React.useMemo(() => myersDiff(oldLines, newLines), [oldLines, newLines]);

  // Group consecutive delete+insert pairs into "change" hunks for char-level highlighting
  type DisplayLine =
    | { type: "context"; content: string }
    | { type: "delete"; content: string; charParts?: { text: string; hi: boolean }[] }
    | { type: "add"; content: string; charParts?: { text: string; hi: boolean }[] };

  const displayDiff = React.useMemo(() => {
    const lines: DisplayLine[] = [];
    let i = 0;
    while (i < rawDiff.length) {
      const entry = rawDiff[i];
      if (entry.op === "equal") {
        lines.push({ type: "context", content: entry.line });
        i++;
      } else if (entry.op === "delete") {
        // Look ahead for a consecutive insert (change pair)
        const deletes: string[] = [];
        while (i < rawDiff.length && rawDiff[i].op === "delete") {
          deletes.push(rawDiff[i].line);
          i++;
        }
        const inserts: string[] = [];
        while (i < rawDiff.length && rawDiff[i].op === "insert") {
          inserts.push(rawDiff[i].line);
          i++;
        }
        // Pair up for char-level diff where possible
        const paired = Math.min(deletes.length, inserts.length);
        for (let p = 0; p < paired; p++) {
          const cd = charDiff(deletes[p], inserts[p]);
          lines.push({ type: "delete", content: deletes[p], charParts: cd.old });
          lines.push({ type: "add", content: inserts[p], charParts: cd.new });
        }
        for (let p = paired; p < deletes.length; p++) {
          lines.push({ type: "delete", content: deletes[p] });
        }
        for (let p = paired; p < inserts.length; p++) {
          lines.push({ type: "add", content: inserts[p] });
        }
      } else {
        // insert without preceding delete
        lines.push({ type: "add", content: entry.line });
        i++;
      }
    }
    return lines;
  }, [rawDiff]);

  const addedCount = displayDiff.filter((d) => d.type === "add").length;
  const removedCount = displayDiff.filter((d) => d.type === "delete").length;

  const rowClass = (type: "add" | "delete" | "context") =>
    cn(
      "px-2 py-0.5 text-xs font-mono text-[11px] border-b border-[var(--border-subtle)] last:border-b-0 selectable",
      {
        "bg-[var(--status-danger)]/20 text-[var(--status-danger)]": type === "delete",
        "bg-[var(--status-success)]/20 text-[var(--status-success)]": type === "add",
        "bg-[var(--bg-main)] text-[var(--text-secondary)]": type === "context",
      },
    );

  const gutterIcon = (type: "add" | "delete" | "context") => {
    if (type === "delete") return "−";
    if (type === "add") return "+";
    return " ";
  };

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
            {displayDiff.map((d, i) => (
              <tr key={i} className={rowClass(d.type)}>
                <td className="w-4 shrink-0 p-1 text-[var(--text-faint)]">
                  {gutterIcon(d.type)}
                </td>
                <td className="p-1 truncate whitespace-pre break-words font-mono text-[11px]">
                  {"charParts" in d && d.charParts
                    ? d.charParts.map((part, pi) =>
                        part.hi ? (
                          <span
                            key={pi}
                            className={cn(
                              "rounded-sm px-[1px]",
                              d.type === "delete"
                                ? "bg-[var(--status-danger)]/40"
                                : "bg-[var(--status-success)]/40",
                            )}
                          >
                            {part.text}
                          </span>
                        ) : (
                          <span key={pi}>{part.text}</span>
                        ),
                      )
                    : d.content}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
