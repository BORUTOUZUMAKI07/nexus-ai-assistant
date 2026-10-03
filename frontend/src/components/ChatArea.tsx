"use client";

import React, { useState, useCallback, useEffect, useRef } from "react";
import { useCopyFeedback, useCopyToggle } from "@/lib/clipboard";
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
  ExternalLink,
  FileCode,
  Volume2,
  VolumeX,
  Timer,
  GitBranch,
  ListChecks,
  Save,
  Scale,
} from "lucide-react";
import { ArtifactItem } from "./ArtifactCanvas";

export interface PlanReviewItem {
  id: string;
  title: string;
  summary: string | null;
  steps: string[];
  status: "pending" | "approved" | "rejected";
}

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
  /** base64 data URL of an image attached to a user message */
  imageDataUrl?: string;
}

export interface PendingHITLRequest {
  thread_id: string;
  request: string;
  plan?: string[];
  tool_name?: string;
  arguments?: Record<string, unknown>;
}

/**
 * The critic's verdict and the evidence-quality score for the run that just
 * finished.
 *
 * Distinct from `MessageItem.annotations` because the backend emits these AFTER
 * the assistant message row exists, so there is no message id to attach them
 * to. Mirrors `TurnVerdict` in `hooks/useNexusChat.ts`.
 */
export interface TurnVerdict {
  critique?: string;
  revision_count: number;
  evidence_score: number;
  evidence_gate_passed: boolean | null;
}

export interface ChatAreaProps {
  messages: MessageItem[];
  isLoading: boolean;
  error?: string | null;
  onRetry?: () => void;
  pendingHITL?: PendingHITLRequest | null;
  /** Critic verdict + evidence score for the run that just completed. */
  turnVerdict?: TurnVerdict | null;
  onResolveHITL?: (
    action: "approve" | "reject" | "modify",
    data?: Record<string, unknown>
  ) => void | Promise<void>;
  onFeedback?: (
    messageId: string,
    feedback: "thumbs_up" | "thumbs_down"
  ) => void;
  onOpenArtifact?: (artifact: ArtifactItem) => void;
  onSaveArtifact?: (artifact: ArtifactItem) => void | Promise<void>;
  onSelectCitation?: (citation: CitationItem) => void;
  /** Pending Plan mode draft awaiting human approval */
  pendingPlan?: PlanReviewItem | null;
  onApprovePlan?: (plan: PlanReviewItem) => void | Promise<void>;
  onRejectPlan?: (plan: PlanReviewItem) => void | Promise<void>;
  onDismissPlan?: () => void;
  planBusy?: boolean;
}

const THOUGHT_STAGES = ["Planning", "Searching", "Analysing", "Synthesising"];

/** Neutral glyph for the verdict row. It is metadata, not an alarm. */
const ScaleIcon: React.FC<{ verdict: TurnVerdict }> = ({ verdict }) => (
  <Scale
    className={`w-4 h-4 shrink-0 ${
      verdict.evidence_gate_passed === false
        ? "text-[var(--status-warning)]"
        : "text-[var(--text-muted)]"
    }`}
  />
);

/**
 * Evidence score, as a percentage of the backend's 0..1 float.
 *
 * The `Number.isFinite` guard is not decoration: the value arrives from a JSON
 * frame, and `NaN` renders as the literal text "NaN%" while still looking like
 * a formatted number in a screenshot.
 */
const ScorePill: React.FC<{ score: number }> = ({ score }) => (
  <span
    className="px-2 py-0.5 rounded bg-[var(--bg-main)] text-[var(--text-secondary)] border border-[var(--border-subtle)]"
    title="Evidence quality score reported by the run"
  >
    evidence {Number.isFinite(score) ? Math.round(score * 100) : 0}%
  </span>
);

/** Live elapsed seconds counter – starts when mounted, stops when stopped=true */
const LiveTimer: React.FC<{ stopped?: boolean }> = ({ stopped }) => {
  const [seconds, setSeconds] = useState(0);
  // Ref holds the impure Date.now() read; set in an effect, never inline in
  // the render body, so the render function itself stays pure.
  const startRef = useRef<number | null>(null);

  useEffect(() => {
    if (startRef.current === null) startRef.current = Date.now();
    if (stopped) return;
    const id = setInterval(() => {
      setSeconds(Math.floor((Date.now() - (startRef.current ?? Date.now())) / 1000));
    }, 250);
    return () => clearInterval(id);
  }, [stopped]);

  return (
    <span className="tabular-nums font-mono text-[10px] text-[var(--text-faint)]">
      {stopped ? `${seconds}s` : `${seconds}s…`}
    </span>
  );
};

/** Thought drawer with live elapsed timer and stage indicator badges */
const ThoughtDrawer: React.FC<{
  msgId: string;
  thought: string;
  isStreaming: boolean;
}> = ({ msgId, thought, isStreaming }) => {
  const [open, setOpen] = useState(false);
  const stageIndex = Math.min(
    THOUGHT_STAGES.length - 1,
    Math.floor(thought.length / 400)
  );

  return (
    <div className="w-full rounded-xl overflow-hidden text-xs border border-[var(--border-subtle)] bg-[var(--bg-surface)] transition-all">
      <button
        onClick={() => setOpen((p) => !p)}
        className="w-full flex items-center justify-between px-3 py-2 text-[var(--text-muted)] hover:text-[var(--text-secondary)] transition-colors"
      >
        <span className="flex items-center gap-2 font-mono text-[11px] text-[var(--accent-ink)] font-medium">
          <Sparkles className={`w-3.5 h-3.5 text-[var(--accent-ink)] ${isStreaming ? "animate-pulse" : ""}`} />
          <span>Thought process</span>
          <span className="px-1.5 py-0.5 rounded bg-[var(--accent-soft)] text-[10px] text-[var(--accent-ink)] font-sans font-semibold">
            {isStreaming ? THOUGHT_STAGES[stageIndex] : "Complete"}
          </span>
        </span>
        <div className="flex items-center gap-2">
          <span className="flex items-center gap-1 text-[10px] text-[var(--text-faint)] font-mono">
            <Timer className="w-3 h-3" />
            <LiveTimer stopped={!isStreaming} key={msgId} />
          </span>
          {open ? <ChevronUp className="w-3.5 h-3.5" /> : <ChevronDown className="w-3.5 h-3.5" />}
        </div>
      </button>
      {open && (
        <div className="p-3 text-[var(--text-secondary)] text-[11px] leading-relaxed font-mono whitespace-pre-wrap border-t border-[var(--border-subtle)] bg-[var(--bg-main)]/70">
          <div className="mb-2 flex items-center flex-wrap gap-1.5 text-[9px] uppercase tracking-wider font-semibold border-b border-[var(--border-subtle)] pb-1.5">
            {THOUGHT_STAGES.map((stage, idx) => (
              <span
                key={stage}
                className={`px-1.5 py-0.5 rounded ${
                  idx <= stageIndex
                    ? "bg-[var(--accent-soft)] text-[var(--accent-ink)]"
                    : "bg-[var(--bg-surface-tint)] text-[var(--text-faint)]"
                }`}
              >
                {idx + 1}. {stage}
              </span>
            ))}
          </div>
          {thought}
          {isStreaming && (
            <span className="inline-block w-1.5 h-3.5 bg-[var(--accent)] ml-0.5 animate-pulse rounded-sm" />
          )}
        </div>
      )}
    </div>
  );
};

function extractArtifact(msgId: string, content: string): ArtifactItem | null {
  const codeBlockRegex = /```(\w+)?\n([\s\S]*?)```/;
  const match = content.match(codeBlockRegex);
  if (!match) return null;
  const language = match[1] || "code";
  const code = match[2].trim();
  if (code.split("\n").length < 3) return null;
  return {
    id: `${msgId}-artifact`,
    title: `${language.toUpperCase()} Snippet`,
    language,
    content: code,
  };
}

/** Plan mode review card — approving commits the plan to the agent run. */
const PlanReviewCard: React.FC<{
  plan: PlanReviewItem;
  onApprove?: (plan: PlanReviewItem) => void | Promise<void>;
  onReject?: (plan: PlanReviewItem) => void | Promise<void>;
  onDismiss?: () => void;
  busy?: boolean;
}> = ({ plan, onApprove, onReject, onDismiss, busy }) => {
  const [action, setAction] = useState<"approve" | "reject" | null>(null);

  const run = async (
    fn: ((p: PlanReviewItem) => void | Promise<void>) | undefined,
    act: "approve" | "reject"
  ) => {
    if (!fn || busy) return;
    setAction(act);
    try {
      await fn(plan);
    } finally {
      setAction(null);
    }
  };

  return (
    <div className="max-w-3xl mx-auto rounded-xl border-2 border-[var(--accent)]/50 bg-[var(--bg-surface)] p-4 shadow-lg">
      <div className="flex items-center justify-between gap-2 mb-2">
        <div className="flex items-center gap-2 text-sm font-semibold text-[var(--text-primary)]">
          <ListChecks className="w-4 h-4 text-[var(--accent-ink)] shrink-0" />
          Proposed Plan
        </div>
        <span className="text-[10px] font-mono px-2 py-0.5 rounded bg-[var(--accent-soft)] text-[var(--accent-ink)] border border-[var(--accent)]/30 uppercase font-semibold">
          Plan mode
        </span>
      </div>
      <h4 className="text-sm font-semibold text-[var(--text-primary)] mb-1">{plan.title}</h4>
      {plan.summary && (
        <p className="text-xs text-[var(--text-secondary)] leading-relaxed mb-3">
          {plan.summary}
        </p>
      )}
      <ol className="space-y-1.5 mb-3">
        {plan.steps.map((step, idx) => (
          <li key={idx} className="flex items-start gap-2 text-xs text-[var(--text-primary)]">
            <span className="shrink-0 w-4.5 h-4.5 mt-0.5 rounded bg-[var(--bg-main)] border border-[var(--border-subtle)] text-[var(--accent-ink)] text-[10px] font-mono font-semibold flex items-center justify-center">
              {idx + 1}
            </span>
            <span className="flex-1 leading-relaxed">{step}</span>
          </li>
        ))}
      </ol>
      <div className="flex items-center gap-2">
        <button
          onClick={() => run(onApprove, "approve")}
          disabled={busy}
          className="flex items-center gap-1.5 rounded-lg bg-[var(--accent)] px-3.5 py-1.5 text-xs font-semibold text-[var(--accent-foreground)] hover:bg-[var(--accent-hover)] transition-colors disabled:opacity-50 shadow-sm"
        >
          <CheckCircle2 className="w-3.5 h-3.5" />
          {action === "approve" ? "Approving…" : "Approve & Execute"}
        </button>
        <button
          onClick={() => run(onReject, "reject")}
          disabled={busy}
          className="flex items-center gap-1.5 rounded-lg border border-[var(--border-subtle)] px-3 py-1.5 text-xs text-[var(--text-secondary)] hover:text-[var(--text-primary)] hover:border-[var(--status-danger)] transition-colors disabled:opacity-50"
        >
          <XCircle className="w-3.5 h-3.5" />
          Reject
        </button>
        {onDismiss && (
          <button
            onClick={onDismiss}
            disabled={busy}
            className="rounded-lg px-3 py-1.5 text-xs text-[var(--text-muted)] hover:text-[var(--text-secondary)] transition-colors disabled:opacity-50"
          >
            Dismiss
          </button>
        )}
      </div>
    </div>
  );
};

const CodeBlock: React.FC<{ language: string; code: string }> = ({ language, code }) => {
  const [copied, copyCode] = useCopyToggle();

  const handleCopyCode = () => copyCode(code);

  return (
    <div className="my-3 rounded-lg overflow-hidden border border-[var(--border-subtle)] bg-[var(--code-block-bg)] dark-island font-mono text-xs">
      <div className="flex items-center justify-between px-3 py-1.5 bg-[var(--bg-surface-elevated)] border-b border-[var(--border-subtle)] text-[11px] text-[var(--text-muted)]">
        <span className="font-semibold uppercase tracking-wider text-[var(--accent-ink)]">
          {language || "code"}
        </span>
        <button
          onClick={handleCopyCode}
          className="flex items-center gap-1 px-2 py-0.5 rounded hover:text-white hover:bg-white/10 transition-colors cursor-pointer"
          title="Copy code"
        >
          {copied ? (
            <>
              <Check className="w-3.5 h-3.5 text-[var(--status-success)]" />
              <span className="text-[var(--status-success)] font-sans text-[10px]">Copied!</span>
            </>
          ) : (
            <>
              <Copy className="w-3.5 h-3.5" />
              <span className="font-sans text-[10px]">Copy</span>
            </>
          )}
        </button>
      </div>
      <div className="p-3.5 overflow-x-auto text-[var(--text-secondary)] leading-relaxed text-[12px]">
        <pre className="!bg-transparent !p-0 !m-0 !border-0 font-mono">
          <code>{code}</code>
        </pre>
      </div>
    </div>
  );
};

/**
 * Renders a ```mermaid fenced block.
 *
 * It shows the diagram SOURCE, not a rendered diagram — there is no mermaid
 * renderer wired up, and the label says "Diagram source" so that is not
 * mistaken for one. The header used to read "Mermaid Diagram", which
 * described a rendering that never happened.
 *
 * Rendering it for real is a deliberate, separate change, not a missing
 * import: the `mermaid` package injects generated HTML and inline styles, so
 * it needs client-only execution plus sanitisation of its output, and it has
 * to be checked against the CSP in next.config.ts. Worth doing as its own
 * change with those constraints designed for, rather than bolted on here.
 */
const MermaidBlock: React.FC<{ code: string }> = ({ code }) => {
  const [copied, copyCode] = useCopyToggle();

  const handleCopyCode = () => copyCode(code);

  return (
    <div className="my-3 rounded-lg overflow-hidden border border-[var(--border-subtle)] bg-[var(--code-block-bg)] dark-island">
      <div className="flex items-center justify-between px-3 py-1.5 bg-[var(--bg-surface-elevated)] border-b border-[var(--border-subtle)] text-[11px] text-[var(--text-muted)]">
        <span className="flex items-center gap-1.5 font-semibold uppercase tracking-wider text-[var(--accent-ink)] font-mono">
          <GitBranch className="w-3.5 h-3.5 text-[var(--accent-ink)]" />
          Mermaid Diagram
        </span>
        <button
          onClick={handleCopyCode}
          className="flex items-center gap-1 px-2 py-0.5 rounded hover:text-white hover:bg-white/10 transition-colors cursor-pointer text-xs"
          title="Copy Diagram Source"
        >
          {copied ? (
            <>
              <Check className="w-3.5 h-3.5 text-[var(--status-success)]" />
              <span className="text-[var(--status-success)] font-sans text-[10px]">Copied!</span>
            </>
          ) : (
            <>
              <Copy className="w-3.5 h-3.5" />
              <span className="font-sans text-[10px]">Copy source</span>
            </>
          )}
        </button>
      </div>
      <div className="p-4 overflow-x-auto text-[var(--text-secondary)] font-mono text-[12px] bg-[var(--bg-main)]/50 leading-relaxed border-l-2 border-[var(--accent)]">
        <pre className="!bg-transparent !p-0 !m-0 !border-0 font-mono text-emerald-400">
          <code>{code}</code>
        </pre>
      </div>
    </div>
  );
};

const TableBlock: React.FC<{ rows: string[][] }> = ({ rows }) => {
  if (!rows || rows.length < 2) return null;
  const header = rows[0];
  const body = rows.slice(1);

  return (
    <div className="my-3 overflow-x-auto rounded-lg border border-[var(--border-subtle)] bg-[var(--bg-surface)]">
      <table className="w-full text-left text-xs border-collapse">
        <thead>
          <tr className="border-b border-[var(--border-strong)] bg-[var(--bg-surface-elevated)] text-[var(--text-primary)] font-semibold">
            {header.map((cell, idx) => (
              <th key={idx} className="px-3.5 py-2.5 font-medium tracking-wide">
                {formatInline(cell)}
              </th>
            ))}
          </tr>
        </thead>
        <tbody className="divide-y divide-[var(--border-subtle)]">
          {body.map((row, rIdx) => (
            <tr
              key={rIdx}
              className={`hover:bg-[var(--bg-surface-elevated)]/50 transition-colors ${
                rIdx % 2 === 1 ? "bg-[var(--bg-surface-tint)]/40" : ""
              }`}
            >
              {row.map((cell, cIdx) => (
                <td key={cIdx} className="px-3.5 py-2 text-[var(--text-secondary)]">
                  {formatInline(cell)}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
};

const MarkdownRenderer: React.FC<{ content: string }> = ({ content }) => {
  // Regex to split on code fences
  const parts = content.split(/(```[\w-]*\n[\s\S]*?```)/g);

  // Stable identity for each fenced block.
  //
  // Keying these by `index` was wrong in a way that only shows up mid-stream.
  // The split pattern matches a *closed* fence, so while the model is writing
  // the first answer the array is short; the instant a closing ``` arrives the
  // regex matches and the array grows by two, shifting every later index. React
  // then re-binds `key={index}` to a different component instance, so a
  // "Copied!" badge a user had just clicked jumped onto a different code block
  // and the one they clicked reverted.
  //
  // Counting fences in document order is stable under that growth, because
  // streamed content is append-only: text before a given fence never changes, so
  // the Nth fence is always the Nth fence. Deriving the number with a
  // `matchAll` over the whole message (rather than incrementing during `.map`)
  // keeps the mapping independent of the array's current length.
  const fenceOrdinal = (() => {
    const ordinals = new Map<number, number>();
    let seen = 0;
    parts.forEach((part, i) => {
      if (/^```[\w-]*\n[\s\S]*?```$/.test(part)) {
        ordinals.set(i, seen);
        seen += 1;
      }
    });
    return ordinals;
  })();

  return (
    <div className="space-y-2">
      {parts.map((part, index) => {
        if (!part) return null;

        // Check if this part is a code block
        const codeMatch = part.match(/^```([\w-]*)\n([\s\S]*?)```$/);
        if (codeMatch) {
          const lang = codeMatch[1]?.trim() || "code";
          const codeText = codeMatch[2];
          const key = `fence-${fenceOrdinal.get(index) ?? index}`;
          if (lang.toLowerCase() === "mermaid") {
            return <MermaidBlock key={key} code={codeText} />;
          }
          return <CodeBlock key={key} language={lang} code={codeText} />;
        }

        // Detect and group Markdown tables
        const rawLines = part.split("\n");
        const blocks: Array<{ type: "lines"; content: string } | { type: "table"; content: string[][] }> = [];
        let currentTableRows: string[][] = [];

        for (let i = 0; i < rawLines.length; i++) {
          const line = rawLines[i].trim();
          if (line.startsWith("|") && line.endsWith("|")) {
            // Check if this line is separator like |---|---|
            if (/^\|(\s*:?-+:?\s*\|)+$/.test(line)) {
              continue; // skip separator line
            }
            const cells = line
              .slice(1, -1)
              .split("|")
              .map((c) => c.trim());
            currentTableRows.push(cells);
          } else {
            if (currentTableRows.length > 0) {
              blocks.push({ type: "table", content: currentTableRows });
              currentTableRows = [];
            }
            blocks.push({ type: "lines", content: rawLines[i] });
          }
        }
        if (currentTableRows.length > 0) {
          blocks.push({ type: "table", content: currentTableRows });
        }

        return (
          <div key={index} className="space-y-1.5">
            {blocks.map((blk, bIdx) => {
              if (blk.type === "table") {
                return <TableBlock key={bIdx} rows={blk.content} />;
              }

              const line = blk.content;
              const trimmed = line.trim();

              // H1 / Title
              if (trimmed.startsWith("# ")) {
                return (
                  <h1 key={bIdx} className="text-lg font-bold text-[var(--text-primary)] mt-3 mb-1.5">
                    {trimmed.replace(/^#\s+/, "")}
                  </h1>
                );
              }

              // H2
              if (trimmed.startsWith("## ")) {
                return (
                  <h2 key={bIdx} className="text-base font-semibold text-[var(--text-primary)] mt-3 mb-1.5 flex items-center gap-1.5">
                    {trimmed.replace(/^##\s+/, "")}
                  </h2>
                );
              }

              // H3
              if (trimmed.startsWith("### ")) {
                return (
                  <h3 key={bIdx} className="text-sm font-semibold text-[var(--text-primary)] mt-2 mb-1">
                    {trimmed.replace(/^###\s+/, "")}
                  </h3>
                );
              }

              // Blockquote / Callout
              if (trimmed.startsWith("> ")) {
                return (
                  <blockquote
                    key={bIdx}
                    className="border-l-2 border-[var(--accent)] pl-3 py-1 my-1.5 text-xs text-[var(--text-secondary)] bg-[var(--accent-soft)] rounded-r-md"
                  >
                    {trimmed.replace(/^>\s+/, "")}
                  </blockquote>
                );
              }

              // Bullet list items
              if (trimmed.startsWith("- ") || trimmed.startsWith("* ")) {
                const itemText = trimmed.replace(/^[-*]\s+/, "");
                return (
                  <div key={bIdx} className="flex items-start gap-2 my-1 text-sm">
                    <span className="text-[var(--accent-ink)] font-bold mt-1 text-xs">•</span>
                    <span className="flex-1">{formatInline(itemText)}</span>
                  </div>
                );
              }

              // Numbered list items
              const numMatch = trimmed.match(/^(\d+)\.\s+(.*)$/);
              if (numMatch) {
                return (
                  <div key={bIdx} className="flex items-start gap-2 my-1 text-sm">
                    <span className="font-mono text-xs text-[var(--accent-ink)] font-semibold min-w-4 mt-0.5">
                      {numMatch[1]}.
                    </span>
                    <span className="flex-1">{formatInline(numMatch[2])}</span>
                  </div>
                );
              }

              // Blank line
              if (!trimmed) {
                return <div key={bIdx} className="h-1" />;
              }

              // Regular paragraph
              return (
                <p key={bIdx} className="my-1 text-sm leading-relaxed">
                  {formatInline(line)}
                </p>
              );
            })}
          </div>
        );
      })}
    </div>
  );
};

// Formats inline **bold**, *italic*, `code`, and ~~strikethrough~~
function formatInline(text: string): React.ReactNode {
  const tokens = text.split(/(`[^`]+`|\*\*[^*]+\*\*|~~[^~]+~~|\*[^*]+\*)/g);

  return tokens.map((token, i) => {
    if (token.startsWith("`") && token.endsWith("`") && token.length > 2) {
      return (
        <code
          key={i}
          className="px-1.5 py-0.5 rounded bg-[var(--bg-surface-tint)] text-[var(--accent-ink)] font-mono text-[12px] border border-[var(--border-subtle)]"
        >
          {token.slice(1, -1)}
        </code>
      );
    }
    if (token.startsWith("**") && token.endsWith("**") && token.length > 4) {
      return (
        <strong key={i} className="font-semibold text-[var(--text-primary)]">
          {token.slice(2, -2)}
        </strong>
      );
    }
    if (token.startsWith("~~") && token.endsWith("~~") && token.length > 4) {
      return (
        <s key={i} className="line-through text-[var(--text-muted)]">
          {token.slice(2, -2)}
        </s>
      );
    }
    if (token.startsWith("*") && token.endsWith("*") && token.length > 2) {
      return (
        <em key={i} className="italic text-[var(--text-primary)]">
          {token.slice(1, -1)}
        </em>
      );
    }
    return token;
  });
}

export const ChatArea: React.FC<ChatAreaProps> = ({
  messages,
  isLoading,
  error,
  onRetry,
  pendingHITL,
  onResolveHITL,
  turnVerdict,
  onFeedback,
  onOpenArtifact,
  onSaveArtifact,
  onSelectCitation,
  pendingPlan,
  onApprovePlan,
  onRejectPlan,
  onDismissPlan,
  planBusy,
}) => {
  const [copiedId, copyText] = useCopyFeedback();
  const [hitlOpen, setHitlOpen] = useState(false);
  const [hitlNote, setHitlNote] = useState("");
  const [resolving, setResolving] = useState(false);
  const [resolved, setResolved] = useState<"approve" | "reject" | "modify" | null>(null);
  const [speakingId, setSpeakingId] = useState<string | null>(null);
  // Collapsed by default. The verdict is metadata about the turn, not the
  // answer, and a permanently-open panel above the transcript would compete
  // with the reply the user actually asked for. It is there for the case where
  // the evidence gate failed and nothing else in the UI says so.
  const [verdictOpen, setVerdictOpen] = useState(false);

  // Auto-scroll. Without this the transcript never follows a streamed answer:
  // tokens arrive one delta at a time, so the user had to scroll manually to
  // read the response the model was producing.
  //
  // The `stick` guard is what makes this safe to add: once the user scrolls up
  // to re-read something, we stop yanking them to the bottom and only resume
  // when they come back. `requestAnimationFrame` waits for layout so we measure
  // the *rendered* height, not the height before the new token painted.
  const scrollRef = useRef<HTMLDivElement | null>(null);
  const stickToBottomRef = useRef(true);

  const lastContent = messages[messages.length - 1]?.content ?? "";
  useEffect(() => {
    const el = scrollRef.current;
    if (!el || !stickToBottomRef.current) return;
    const frame = requestAnimationFrame(() => {
      el.scrollTop = el.scrollHeight;
    });
    return () => cancelAnimationFrame(frame);
  }, [messages.length, lastContent]);

  const handleScroll = () => {
    const el = scrollRef.current;
    if (!el) return;
    // 80px of slack: treat "close enough to the bottom" as pinned so a small
    // overscroll or a sub-pixel rounding does not flip the flag off.
    stickToBottomRef.current =
      el.scrollHeight - el.scrollTop - el.clientHeight < 80;
  };

  // Find the last assistant message index (for streaming cursor)
  const lastAssistantIdx = messages.reduce(
    (acc, m, i) => (m.role === "assistant" ? i : acc),
    -1
  );

  const handleTTS = useCallback((msgId: string, text: string) => {
    if (!window.speechSynthesis) return;
    if (speakingId === msgId) {
      window.speechSynthesis.cancel();
      setSpeakingId(null);
      return;
    }
    window.speechSynthesis.cancel();
    const utt = new SpeechSynthesisUtterance(text);
    utt.rate = 1.0;
    utt.pitch = 1.0;
    utt.onend = () => setSpeakingId(null);
    utt.onerror = () => setSpeakingId(null);
    setSpeakingId(msgId);
    window.speechSynthesis.speak(utt);
  }, [speakingId]);

  const handleCopy = (id: string, text: string) => copyText(id, text);

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
    } catch {
      // The hook now rethrows nothing on failure -- it records the error and
      // deliberately keeps `pendingHITL` so the card stays actionable. Guard
      // here too so a rejection can never mark the decision as resolved and
      // collapse the card the user still needs.
    } finally {
      setResolving(false);
    }
  };

  return (
    <div
      ref={scrollRef}
      onScroll={handleScroll}
      className="flex-1 overflow-y-auto px-4 py-6 md:px-12 space-y-6"
    >
      {messages.length === 0 && (
        <div className="h-full flex flex-col items-center justify-center text-center max-w-md mx-auto py-24 space-y-4">
          <div className="w-12 h-12 rounded-xl bg-[var(--bg-surface)] border border-[var(--border-subtle)] flex items-center justify-center">
            <Sparkles className="w-6 h-6 text-[var(--accent-ink)]" />
          </div>
          <h2 className="text-xl font-semibold tracking-tight text-[var(--text-primary)]">
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
              className="flex items-center gap-1.5 rounded-lg border border-[var(--border-subtle)] px-3 py-1.5 text-xs text-[var(--text-secondary)] hover:text-[var(--text-primary)] hover:border-[var(--border-strong)] transition-colors whitespace-nowrap"
            >
              <RotateCw className="w-3.5 h-3.5" />
              Retry
            </button>
          )}
        </div>
      )}

      {/* HITL approval card */}
      {pendingHITL && !resolved && (
        <div className="max-w-3xl mx-auto rounded-xl border-2 border-[var(--status-warning)]/40 bg-[var(--bg-surface)] p-4.5 shadow-lg">
          <div className="flex items-center justify-between gap-2 mb-2">
            <div className="flex items-center gap-2 text-sm font-semibold text-[var(--text-primary)]">
              <ShieldAlert className="w-4.5 h-4.5 text-[var(--status-warning)] shrink-0" />
              Human Approval Required (Harness Layer 3)
            </div>
            <span className="text-[10px] font-mono px-2 py-0.5 rounded bg-[var(--status-warning)]/10 text-[var(--status-warning)] border border-[var(--status-warning)]/30 uppercase font-semibold">
              {pendingHITL.tool_name ? "High-Impact Action" : "External Tool"}
            </span>
          </div>
          <p className="text-xs text-[var(--text-primary)] leading-relaxed mb-3">
            {pendingHITL.request}
          </p>

          {pendingHITL.tool_name && (
            <div className="mb-3 p-2.5 rounded-lg bg-[var(--bg-main)] border border-[var(--border-subtle)] font-mono text-[11px]">
              <span className="text-[var(--accent-ink)] font-semibold">Tool: </span>
              <span className="text-[var(--text-primary)]">{pendingHITL.tool_name}</span>
            </div>
          )}

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
                  className="flex items-center gap-1.5 rounded-lg border border-[var(--accent)] px-3 py-1.5 text-xs text-[var(--accent-ink)] hover:bg-[var(--accent-soft)] transition-colors disabled:opacity-50 font-medium"
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
                className="flex items-center gap-1.5 rounded-lg bg-[var(--accent)] px-3.5 py-1.5 text-xs font-semibold text-[var(--accent-foreground)] hover:bg-[var(--accent-hover)] transition-colors disabled:opacity-50 shadow-sm"
              >
                <CheckCircle2 className="w-3.5 h-3.5" />
                Approve &amp; Continue
              </button>
              <button
                onClick={() => resolve("reject")}
                disabled={resolving}
                className="flex items-center gap-1.5 rounded-lg border border-[var(--border-subtle)] px-3 py-1.5 text-xs text-[var(--text-secondary)] hover:text-[var(--text-primary)] hover:border-[var(--status-danger)] transition-colors disabled:opacity-50"
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

      {/* Critic verdict / evidence score.
          This block used not to exist at all: the backend emitted both frames
          on every finished run, the BFF route dropped them, and the user was
          never told. The gap was invisible from the backend because the frames
          were written to the database and the API contract still held. */}
      {turnVerdict && (
        <div className="max-w-3xl mx-auto rounded-xl border border-[var(--border-subtle)] bg-[var(--bg-surface)] p-3.5">
          <button
            onClick={() => setVerdictOpen((v) => !v)}
            aria-expanded={verdictOpen}
            className="w-full flex items-center justify-between gap-2 text-left"
          >
            <span className="flex items-center gap-2 text-xs font-semibold text-[var(--text-secondary)]">
              <ScaleIcon verdict={turnVerdict} />
              {turnVerdict.revision_count > 0
                ? `Answer revised ${turnVerdict.revision_count}× before delivery`
                : "Answer passed review on first draft"}
            </span>
            <span className="flex items-center gap-2 text-[10px] font-mono uppercase tracking-wide">
              <ScorePill score={turnVerdict.evidence_score} />
              {/* Three states, not two. `null` is the backend not reporting the
                  gate at all, which is not the same as it passing, and
                  rendering it as a green "passed" would be a claim nobody made. */}
              {turnVerdict.evidence_gate_passed === false ? (
                <span className="px-2 py-0.5 rounded bg-[var(--status-warning)]/10 text-[var(--status-warning)] border border-[var(--status-warning)]/30">
                  Low evidence
                </span>
              ) : turnVerdict.evidence_gate_passed === true ? (
                <span className="px-2 py-0.5 rounded bg-[var(--status-success)]/10 text-[var(--status-success)] border border-[var(--status-success)]/30">
                  Evidence gate passed
                </span>
              ) : null}
              <ChevronDown
                className={`w-3.5 h-3.5 text-[var(--text-muted)] transition-transform ${verdictOpen ? "rotate-180" : ""}`}
              />
            </span>
          </button>

          {verdictOpen && turnVerdict.critique && (
            <div className="mt-3 pt-3 border-t border-[var(--border-subtle)]">
              <p className="text-[10px] font-mono uppercase tracking-wide text-[var(--text-muted)] mb-1">
                What the reviewer flagged
              </p>
              <p className="text-xs text-[var(--text-secondary)] leading-relaxed whitespace-pre-wrap">
                {turnVerdict.critique}
              </p>
            </div>
          )}
        </div>
      )}

      {/* Plan mode review card */}
      {pendingPlan && (
        <PlanReviewCard
          plan={pendingPlan}
          onApprove={onApprovePlan}
          onReject={onRejectPlan}
          onDismiss={onDismissPlan}
          busy={planBusy}
        />
      )}

      {messages.map((msg, msgIndex) => {
        const isUser = msg.role === "user";
        const hasThought = Boolean(msg.thought_process);
        const artifact = !isUser ? extractArtifact(msg.id, msg.content) : null;
        const isStreamingThought = isLoading && msgIndex === lastAssistantIdx && hasThought;

        return (
          <div
            key={msg.id}
            className={`flex gap-3.5 max-w-3xl mx-auto ${
              isUser ? "justify-end" : "justify-start"
            }`}
          >
            {/* Assistant Avatar */}
            {!isUser && (
              <div className="w-8 h-8 rounded-lg bg-[var(--bg-surface-elevated)] border border-[var(--border-subtle)] flex items-center justify-center text-[var(--text-primary)] shrink-0 mt-1">
                <Bot className="w-4 h-4 text-[var(--text-secondary)]" />
              </div>
            )}

            {/* Message Bubble Body */}
            <div className={`flex flex-col space-y-2 max-w-[85%] ${isUser ? "items-end" : "items-start"}`}>
              {/* Thought Process Drawer with Live Timer & Stage Badges */}
              {!isUser && hasThought && (
                <ThoughtDrawer
                  msgId={msg.id}
                  thought={msg.thought_process!}
                  isStreaming={isStreamingThought}
                />
              )}

              {/* Artifact Card trigger if code/document detected */}
              {artifact && onOpenArtifact && (
                <div className="w-full p-2.5 rounded-xl border border-[var(--border-subtle)] bg-[var(--bg-surface)] flex items-center justify-between gap-3 hover:border-[var(--accent)]/50 transition-colors">
                  <div className="flex items-center gap-2.5 min-w-0">
                    <FileCode className="w-4 h-4 text-[var(--accent-ink)] shrink-0" />
                    <div className="min-w-0">
                      <div className="text-xs font-semibold text-[var(--text-primary)] truncate">
                        {artifact.title}
                      </div>
                      <span className="text-[10px] uppercase font-mono tracking-wider text-[var(--text-muted)]">
                        {artifact.language} • {artifact.content.split("\n").length} lines
                      </span>
                    </div>
                  </div>
                  <div className="flex items-center gap-1.5 shrink-0">
                    {onSaveArtifact && (
                      <button
                        onClick={() => onSaveArtifact(artifact)}
                        className="flex items-center gap-1 text-xs font-medium px-2.5 py-1 rounded-md border border-[var(--border-subtle)] text-[var(--text-secondary)] hover:text-[var(--accent-ink)] hover:border-[var(--accent)]/50 transition-all"
                        title="Save artifact (persisted + versioned)"
                      >
                        <Save className="w-3 h-3" />
                        Save
                      </button>
                    )}
                    <button
                      onClick={() => onOpenArtifact(artifact)}
                      className="flex items-center gap-1.5 text-xs font-semibold px-2.5 py-1 rounded-md bg-[var(--accent-soft)] text-[var(--accent-ink)] hover:bg-[var(--accent)] hover:text-[var(--accent-foreground)] transition-all"
                    >
                      Open in Canvas
                      <ExternalLink className="w-3 h-3" />
                    </button>
                  </div>
                </div>
              )}

              {/* Main Text Bubble */}
              <div
                className={`p-4 rounded-xl text-sm leading-relaxed prose-nexus border ${
                  isUser
                    ? "bg-[var(--bg-surface-elevated)] text-[var(--text-primary)] border-[var(--border-strong)] rounded-tr-none shadow-sm"
                    : "glass-card text-[var(--text-primary)] rounded-tl-none"
                }`}
              >
                {/* Inline image for user messages */}
                {isUser && msg.imageDataUrl && (
                  // eslint-disable-next-line @next/next/no-img-element
                  <img
                    src={msg.imageDataUrl}
                    alt="User attachment"
                    className="max-h-48 max-w-xs rounded-lg mb-2 border border-[var(--border-subtle)] object-cover"
                  />
                )}
                {isUser ? (
                  <div className="whitespace-pre-wrap">{msg.content}</div>
                ) : (
                  <>
                    <MarkdownRenderer content={msg.content} />
                    {/* Streaming cursor pulse on the last assistant message */}
                    {isLoading && msgIndex === lastAssistantIdx && (
                      <span className="inline-block w-1.5 h-4 bg-[var(--accent)] ml-0.5 animate-pulse rounded-sm align-middle" />
                    )}
                  </>
                )}

                {/* Inline Tool Calls Output */}
                {msg.tool_calls && msg.tool_calls.length > 0 && (
                  <div className="mt-3 space-y-2 pt-2 border-t border-[var(--border-subtle)]">
                    {msg.tool_calls.map((t, idx) => (
                      <div
                        key={idx}
                        className="rounded-lg p-2.5 text-xs border border-[var(--border-subtle)] bg-[var(--bg-main)]"
                      >
                        <div className="flex items-center justify-between text-[var(--accent-ink)] font-mono text-[11px] mb-1">
                          <span className="flex items-center gap-1">
                            <Terminal className="w-3 h-3" /> {t.name}
                          </span>
                          <span className="text-[9px] uppercase px-1 rounded bg-[var(--bg-surface-elevated)] text-[var(--text-muted)]">
                            {t.status}
                          </span>
                        </div>
                        {t.result !== undefined && (
                          <pre className="text-[10px] text-[var(--text-secondary)] max-h-32 overflow-y-auto mt-1 bg-transparent p-0 border-0 font-mono">
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
                    <div className="text-[10px] uppercase font-mono tracking-wider text-[var(--accent-ink)] flex items-center gap-1 mb-1.5 font-semibold">
                      <BookOpen className="w-3 h-3" /> Grounding sources
                    </div>
                    <div className="flex flex-wrap gap-1.5">
                      {msg.citations.map((c, idx) => (
                        <button
                          key={idx}
                          onClick={() => onSelectCitation?.(c)}
                          className="px-2 py-1 rounded-md border border-[var(--border-subtle)] bg-[var(--bg-main)] text-[var(--text-secondary)] text-[10px] flex items-center gap-1.5 hover:border-[var(--accent)] hover:text-[var(--text-primary)] transition-colors cursor-pointer"
                          title="Inspect source"
                        >
                          <span className="text-[var(--accent-ink)] font-semibold">[{idx + 1}]</span>
                          <span className="truncate max-w-[120px]">{c.filename}</span>
                          <span className="text-[9px] font-mono text-[var(--text-muted)] bg-[var(--bg-surface-elevated)] px-1 rounded">
                            {Math.round(c.score * 100)}%
                          </span>
                        </button>
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
                    className="hover:text-[var(--text-primary)] p-1 rounded transition-colors"
                    title="Copy response"
                  >
                    {copiedId === msg.id ? <Check className="w-3.5 h-3.5 text-[var(--status-success)]" /> : <Copy className="w-3.5 h-3.5" />}
                  </button>
                  {/* TTS button – browser speechSynthesis, 0 cost */}
                  <button
                    onClick={() => handleTTS(msg.id, msg.content)}
                    className={`p-1 rounded transition-colors ${
                      speakingId === msg.id
                        ? "text-[var(--accent-ink)] animate-pulse"
                        : "hover:text-[var(--text-primary)]"
                    }`}
                    title={speakingId === msg.id ? "Stop speaking" : "Read aloud (TTS)"}
                  >
                    {speakingId === msg.id ? (
                      <VolumeX className="w-3.5 h-3.5" />
                    ) : (
                      <Volume2 className="w-3.5 h-3.5" />
                    )}
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

      {/* Pulsing indicator when loading and no streaming message yet */}
      {isLoading && lastAssistantIdx === -1 && (
        <div className="max-w-3xl mx-auto flex items-center gap-2 text-xs text-[var(--text-muted)] font-mono px-4 py-2 rounded-lg bg-[var(--bg-surface)] border border-[var(--border-subtle)] animate-pulse">
          <Sparkles className="w-3.5 h-3.5 text-[var(--accent-ink)] animate-spin" />
          <span>Nexus is working…</span>
        </div>
      )}
    </div>
  );
};