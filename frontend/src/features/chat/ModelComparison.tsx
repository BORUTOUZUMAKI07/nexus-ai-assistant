"use client";

import * as React from "react";
import { Sliders, X } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader } from "@/components/ui/card";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogTitle,
} from "@/components/ui/dialog";
import { modelDisplayName } from "@/lib/models";

interface ModelComparisonProps {
  onClose: () => void;
  question: string;
  modelA: string;
  modelB: string;
}

const NO_QUESTION = "Ask a question in the chat first — there is nothing to compare yet.";

/**
 * Storage keys for the hidden conversations comparisons run in — one per pane.
 *
 * Comparison used to post with `conversationId: null`, and `/api/chat` creates a
 * conversation for an unknown id — so every comparison left two fresh "New Chat"
 * rows in the user's sidebar. These two archived conversations are created once
 * and reused, which keeps the transcript out of the sidebar without adding a
 * non-persisting code path to the whole run pipeline.
 *
 * One per pane, not one shared: the backend serialises concurrent runs per
 * thread and answers the loser 409 (`backend_rejected_409`), so two panes on one
 * conversation can never both stream.
 */
const COMPARE_CONVERSATION_KEYS = {
  a: "nexus.compareConversationId.a",
  b: "nexus.compareConversationId.b",
} as const;

type CompareSlot = keyof typeof COMPARE_CONVERSATION_KEYS;

function readCompareConversationId(slot: CompareSlot): string | null {
  try {
    return window.localStorage.getItem(COMPARE_CONVERSATION_KEYS[slot]);
  } catch {
    return null;
  }
}

function rememberCompareConversationId(slot: CompareSlot, id: string | null): void {
  try {
    if (id) window.localStorage.setItem(COMPARE_CONVERSATION_KEYS[slot], id);
    else window.localStorage.removeItem(COMPARE_CONVERSATION_KEYS[slot]);
  } catch {
    // Private-mode storage failures are not worth failing a comparison over.
  }
}

/**
 * Resolve (or create) the hidden comparison conversation.
 *
 * Created through the public conversations API and archived immediately so it
 * never appears in the sidebar (which lists `archived=false`). Returns null when
 * creation fails, which falls back to the previous auto-create behaviour rather
 * than breaking comparison.
 */
async function resolveCompareConversation(
  slot: CompareSlot,
  signal: AbortSignal,
): Promise<string | null> {
  const cached = readCompareConversationId(slot);
  if (cached) return cached;
  try {
    const created = await fetch("/api/conversations", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ title: "Model comparison" }),
      signal,
    });
    if (!created.ok) return null;
    const conversation = (await created.json()) as { id?: string };
    if (!conversation.id) return null;
    await fetch(`/api/conversations/${conversation.id}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ is_archived: true }),
      signal,
    });
    rememberCompareConversationId(slot, conversation.id);
    return conversation.id;
  } catch {
    return null;
  }
}

export function ModelComparison({ onClose, question, modelA, modelB }: ModelComparisonProps): React.ReactElement {
  const [responseA, setResponseA] = React.useState<string>("");
  const [responseB, setResponseB] = React.useState<string>("");
  const [loadingA, setLoadingA] = React.useState<boolean>(true);
  const [loadingB, setLoadingB] = React.useState<boolean>(true);
  const [errorA, setErrorA] = React.useState<string | null>(null);
  const [errorB, setErrorB] = React.useState<string | null>(null);

  const aborterRef = React.useRef<AbortController | null>(null);
  // The comparison is opened by a plain button in the chat header, not by a
  // Radix DialogTrigger, so remember what had focus and give it back on close.
  const returnFocusRef = React.useRef<HTMLElement | null>(null);

  React.useEffect(() => {
    aborterRef.current = new AbortController();

    // Nothing to compare: spending two model calls on an empty prompt returns
    // two versions of "what do you need?". The panes say so instead.
    if (!question.trim()) {
      return () => {
        aborterRef.current?.abort();
      };
    }

    async function runComparison() {
      // Each pane streams into its own hidden, archived conversation, so a
      // comparison adds nothing to the sidebar — and the two panes do not
      // contend for the backend's per-thread stream lock.
      const [conversationIdA, conversationIdB] = await Promise.all([
        resolveCompareConversation("a", aborterRef.current!.signal).catch(() => null),
        resolveCompareConversation("b", aborterRef.current!.signal).catch(() => null),
      ]);

      /**
       * Stream one model's answer into its pane.
       *
       * The bodies on this wire are the Vercel AI SDK data-stream frames that
       * `/api/chat` relays -- `0:` text, `3:` error, `8:` annotations -- not the
       * `{type: "text_delta"}` objects the backend emits. This used to parse for
       * the backend's shape and post the backend's field names (`message`,
       * `conversation_id`), so both panes were answered with a 400 that nothing
       * read: they rendered "Generating response…" forever. Measured live.
       */
      const run = async (
        model: string,
        conversationId: string | null,
        slot: CompareSlot,
        setResp: (s: string | ((prev: string) => string)) => void,
        setErr: (e: string | null) => void,
        setLoad: (l: boolean) => void,
      ) => {
        try {
          const res = await fetch("/api/chat", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            // The relay's contract: a `messages` array and a `model`. Anything
            // else is rejected by its own validation before it forwards.
            body: JSON.stringify({
              messages: [{ role: "user", content: question }],
              conversationId,
              mode: "normal",
              model,
            }),
            signal: aborterRef.current!.signal,
          });
          if (res.status === 404 && conversationId) {
            // The stored comparison conversation was deleted (or belongs to a
            // previous session's account). Forget it so the next comparison
            // creates a fresh one instead of 404ing forever.
            rememberCompareConversationId(slot, null);
          }
          if (!res.ok) {
            const detail = await res.text();
            setErr(
              `Chat API error ${res.status}: ${detail.slice(0, 300) || res.statusText}`
            );
            return;
          }
          const reader = res.body?.getReader();
          if (!reader) {
            setErr("Comparison stream unavailable");
            return;
          }
          const decoder = new TextDecoder();
          let buffer = "";
          for (;;) {
            const { value, done } = await reader.read();
            if (done) break;
            buffer += decoder.decode(value, { stream: true });
            const lines = buffer.split("\n");
            buffer = lines.pop() ?? "";
            for (const raw of lines) {
              const line = raw.trim();
              if (!line) continue;
              const payload = line.slice(line.indexOf(":") + 1).trim();
              if (line.startsWith("0:")) {
                try {
                  const chunk = JSON.parse(payload);
                  if (typeof chunk === "string") {
                    setResp((prev: string) => prev + chunk);
                  }
                } catch {
                  // A text frame that is not a JSON string carries nothing to show.
                }
              } else if (line.startsWith("3:")) {
                try {
                  setErr(String(JSON.parse(payload)));
                } catch {
                  setErr(payload || "Comparison failed");
                }
              }
            }
          }
        } catch (err) {
          if ((err as Error).name === "AbortError") return;
          setErr(err instanceof Error ? err.message : "Comparison failed");
        } finally {
          // The stream closing is the end of this pane, whether the last frame
          // was an answer, an error, or nothing at all.
          setLoad(false);
        }
      };

      await Promise.all([
        run(modelA, conversationIdA, "a", setResponseA, setErrorA, setLoadingA),
        run(modelB, conversationIdB, "b", setResponseB, setErrorB, setLoadingB),
      ]);
    }

    runComparison();
    return () => {
      aborterRef.current?.abort();
    };
  }, [question, modelA, modelB]);

  const hasQuestion = Boolean(question.trim());
  const effectiveErrorA = !hasQuestion ? NO_QUESTION : errorA;
  const effectiveErrorB = !hasQuestion ? NO_QUESTION : errorB;
  const effectiveLoadingA = !hasQuestion ? false : loadingA;
  const effectiveLoadingB = !hasQuestion ? false : loadingB;

  return (
    // Radix owns role/aria-modal, the scrim, Escape, focus trapping and focus
    // restore. The content keeps the existing full-height comparison layout.
    <Dialog
      open
      onOpenChange={(open) => {
        if (!open) onClose();
      }}
    >
      <DialogContent
        aria-modal="true"
        className="fixed inset-0 left-0 top-0 z-50 flex h-full w-full max-w-none translate-x-0 translate-y-0 flex-col gap-0 rounded-none border-0 border-l border-[var(--border-subtle)] p-0 bg-[var(--bg-surface)] shadow-2xl"
        onOpenAutoFocus={() => {
          returnFocusRef.current = document.activeElement as HTMLElement | null;
        }}
        onCloseAutoFocus={(event) => {
          const target = returnFocusRef.current;
          if (target && document.contains(target)) {
            event.preventDefault();
            target.focus();
          }
        }}
      >
        <div className="flex items-center justify-between border-b border-[var(--border-subtle)] px-4 py-3 bg-[var(--bg-surface-elevated)] shrink-0">
          <div className="flex items-center gap-2">
            <Sliders className="w-4 h-4 text-[var(--accent)]" />
            <DialogTitle className="text-sm font-semibold text-[var(--text-primary)] uppercase tracking-tighter">
              Model comparison
            </DialogTitle>
          </div>
          <DialogDescription className="sr-only">
            Side-by-side responses from the selected models.
          </DialogDescription>
          <button
            type="button"
            onClick={onClose}
            className="rounded-md p-2 text-[var(--text-secondary)] hover:text-[var(--text-primary)] hover:bg-[var(--bg-surface)]"
            aria-label="Close comparison"
          >
            <X className="w-5 h-5" />
          </button>
        </div>
        <div className="flex-1 overflow-hidden flex flex-col">
          {/* Question strip */}
          <div className="px-4 py-2 border-b border-[var(--border-subtle)] bg-[var(--bg-main)]/50">
            <div className="flex items-start gap-2">
              <div className="w-8 h-8 rounded-lg bg-[var(--accent)]/10 flex items-center justify-center shrink-0">
                <span className="text-[var(--accent-ink)] font-mono text-xs font-bold">Q</span>
              </div>
              <p className="text-sm text-[var(--text-secondary)] leading-relaxed">{question}</p>
            </div>
          </div>
          {/* Side-by-side panels */}
          <div className="flex-1 flex overflow-hidden">
            <ModelComparisonPanel
              // Labelled from the catalog, not from the id: the pane used to
              // hardcode badge="fast_chat" / "complex_reasoning" beside a
              // caller-supplied label, so the two could disagree about which
              // model had answered.
              label={modelDisplayName(modelA) ?? modelA}
              badge={modelA}
              response={responseA}
              loading={effectiveLoadingA}
              error={effectiveErrorA}
            />
            <div className="w-px bg-[var(--border-subtle)] self-stretch" />
            <ModelComparisonPanel
              label={modelDisplayName(modelB) ?? modelB}
              badge={modelB}
              response={responseB}
              loading={effectiveLoadingB}
              error={effectiveErrorB}
            />
          </div>
        </div>
      </DialogContent>
    </Dialog>
  );
}

interface ModelComparisonPanelProps {
  label: string;
  badge: string;
  response: string;
  loading: boolean;
  error: string | null;
}

function ModelComparisonPanel({ label, badge, response, loading, error }: ModelComparisonPanelProps): React.ReactElement {
  return (
    <div className="flex-1 flex flex-col min-w-0">
      <Card className="flex-1 h-full overflow-hidden border-none shadow-none bg-transparent">
        <CardHeader className="px-4 py-2 flex items-center justify-between border-b border-[var(--border-subtle)]">
          <div className="flex items-center gap-2">
            <span className="text-xs font-medium text-[var(--text-primary)]">{label}</span>
            <Badge variant="outline" className="px-2 py-0.5 text-[10px]">
              {badge}
            </Badge>
          </div>
          {loading && (
            <span className="text-xs text-[var(--text-muted)]">streaming…</span>
          )}
        </CardHeader>
        <CardContent className="p-0">
          <div className="h-full overflow-y-auto p-4 flex flex-col gap-3">
            {error && (
              <div className="rounded-lg border border-[var(--status-danger)]/30 bg-[var(--status-danger)]/10 p-3 text-xs text-[var(--status-danger)]">
                {error}
              </div>
            )}
            {!error && (
              // Rendered as text, not HTML: this is model output, and a model
              // that emits `<img onerror=…>` would otherwise execute it here.
              <div className="text-sm text-[var(--text-primary)] leading-relaxed whitespace-pre-wrap break-words">
                {response}
              </div>
            )}
            {loading && !error && (
              <div className="flex h-16 items-center justify-center text-[var(--text-muted)] text-xs italic">
                Generating response…
              </div>
            )}
          </div>
        </CardContent>
      </Card>
    </div>
  );
}
