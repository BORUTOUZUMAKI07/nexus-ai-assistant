"use client";

import * as React from "react";
import { Sliders, X } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

interface ModelComparisonProps {
  onClose: () => void;
  question: string;
  modelA: string;
  modelB: string;
}

export function ModelComparison({ onClose, question, modelA, modelB }: ModelComparisonProps): React.ReactElement {
  const [responseA, setResponseA] = React.useState<string>("");
  const [responseB, setResponseB] = React.useState<string>("");
  const [loadingA, setLoadingA] = React.useState<boolean>(true);
  const [loadingB, setLoadingB] = React.useState<boolean>(true);
  const [errorA, setErrorA] = React.useState<string | null>(null);
  const [errorB, setErrorB] = React.useState<string | null>(null);

  let aborter: AbortController | null = null;

  React.useEffect(() => {
    aborter = new AbortController();

    async function runComparison() {
      const run = async (
        model: string,
        setResp: (s: string | ((prev: string) => string)) => void,
        setErr: (e: string | null) => void,
        setLoad: (l: boolean) => void,
      ) => {
        try {
          const res = await fetch("/api/chat", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              message: question,
              model,
              conversation_id: null,
            }),
            signal: aborter!.signal,
          });
          const reader = res.body?.getReader();
          const decoder = new TextDecoder();
          let buffer = "";
          while (reader) {
            const { value, done } = await reader.read();
            if (done) break;
            buffer += decoder.decode(value, { stream: true });
            const lines = buffer.split("\n");
            buffer = lines.pop() ?? "";
            for (const line of lines) {
              if (!line.trim()) continue;
              try {
                const parsed = JSON.parse(line);
                if (parsed.type === "text_delta") {
                  setResp((prev: string) => prev + (parsed.content ?? ""));
                }
                if (parsed.type === "done") {
                  setLoad(false);
                }
              } catch {
                // ignore malformed frames
              }
            }
          }
        } catch (err) {
          if ((err as Error).name === "AbortError") return;
          setErr(err instanceof Error ? err.message : "Comparison failed");
          setLoad(false);
        }
      };

      await Promise.all([
        run(modelA, setResponseA, setErrorA, setLoadingA),
        run(modelB, setResponseB, setErrorB, setLoadingB),
      ]);
    }

    runComparison();
    return () => {
      aborter?.abort();
    };
  }, [question, modelA, modelB]);

  return (
    <div
      className="fixed inset-0 z-50 flex flex-col"
      role="dialog"
      aria-modal="true"
      aria-label="Model comparison"
    >
      {/* Backdrop */}
      <div
        className="fixed inset-0 bg-black/50 backdrop-blur-sm"
        onClick={onClose}
      />
      <div className="flex-1 flex flex-col bg-[var(--bg-surface)] border-l border-[var(--border-subtle)] shadow-2xl">
        <div className="flex items-center justify-between border-b border-[var(--border-subtle)] px-4 py-3 bg-[var(--bg-surface-elevated)] shrink-0">
            <div className="flex items-center gap-2">
              <Sliders className="w-4 h-4 text-[var(--accent)]" />
            <h2 className="text-sm font-semibold text-[var(--text-primary)] uppercase tracking-tighter">
              Model Comparison
            </h2>
          </div>
          <button
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
              label={modelA}
              badge="fast_chat"
              response={responseA}
              loading={loadingA}
              error={errorA}
            />
            <div className="w-px bg-[var(--border-subtle)] self-stretch" />
            <ModelComparisonPanel
              label={modelB}
              badge="complex_reasoning"
              response={responseB}
              loading={loadingB}
              error={errorB}
            />
          </div>
        </div>
      </div>
    </div>
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
              <div
                className="text-sm text-[var(--text-primary)] leading-relaxed whitespace-pre-wrap break-words"
                dangerouslySetInnerHTML={{ __html: response.replace(/\n/g, "<br>") }}
              />
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
