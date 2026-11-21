"use client";

import React, { useState } from "react";
import { Beaker, GitCompareArrows, Loader2 } from "lucide-react";
import {
  compareEvaluationTraces,
  fetchEvaluationTrace,
  runArenaEvaluation,
  runConversationalEvaluation,
  runPromptRegressionGate,
  runQualityEvaluation,
  runRagEvaluation,
  runRedTeamEvaluation,
} from "@/lib/api";

/**
 * Operator-facing surface for the admin evaluation suite.
 *
 * Every mode maps to exactly one backend endpoint (the BFF relays under
 * /api/admin/evaluation), and the raw report is shown verbatim rather than
 * reshaped: an evaluator's metric set changes more often than this UI does.
 */
type Field = {
  key: string;
  label: string;
  placeholder?: string;
  multiline?: boolean;
  type?: "text" | "number";
  optional?: boolean;
};

type Mode = {
  id: string;
  label: string;
  hint: string;
  button: string;
  fields: Field[];
  run: (values: Record<string, string>) => Promise<unknown>;
};

// Optional fields are absent from `values` until the operator types in them,
// so these helpers must accept undefined rather than assuming a blank string.
const lines = (raw?: string) =>
  (raw ?? "")
    .split("\n")
    .map((line) => line.trim())
    .filter(Boolean);

const numberOr = (raw: string | undefined, fallback: number) => {
  if (raw === undefined || raw.trim() === "") return fallback;
  const parsed = Number(raw);
  return Number.isFinite(parsed) ? parsed : fallback;
};

const MODES: Mode[] = [
  {
    id: "rag",
    label: "RAG quality",
    hint: "Faithfulness, answer relevance and context precision for one RAG turn. Every run is logged upstream.",
    button: "Run RAG evaluation",
    fields: [
      { key: "question", label: "Question", placeholder: "What is the capital of France?" },
      { key: "answer", label: "Answer", placeholder: "Paris.", multiline: true },
      {
        key: "contexts",
        label: "Contexts (one per line)",
        placeholder: "France's capital is Paris.",
        multiline: true,
        optional: true,
      },
      { key: "ground_truth", label: "Ground truth (optional)", placeholder: "Paris", optional: true },
    ],
    run: (v) =>
      runRagEvaluation({
        question: v.question,
        answer: v.answer,
        contexts: lines(v.contexts),
        ground_truth: v.ground_truth || undefined,
      }),
  },
  {
    id: "quality",
    label: "Response quality",
    hint: "Deterministic tokens/sec, wording efficiency and formatting scores for one response.",
    button: "Run quality evaluation",
    fields: [
      { key: "prompt", label: "Prompt (optional)", multiline: true, optional: true },
      { key: "response", label: "Response", multiline: true },
      { key: "tokens_used", label: "Tokens used", type: "number", placeholder: "0", optional: true },
      { key: "latency_ms", label: "Latency (ms)", type: "number", placeholder: "0", optional: true },
    ],
    run: (v) =>
      runQualityEvaluation({
        prompt: v.prompt || undefined,
        response: v.response,
        tokens_used: numberOr(v.tokens_used, 0),
        latency_ms: numberOr(v.latency_ms, 0),
      }),
  },
  {
    id: "arena",
    label: "Arena pair",
    hint: "Pairwise judge: does output A beat output B for the same query?",
    button: "Run arena evaluation",
    fields: [
      { key: "query", label: "Query", placeholder: "Explain the migration plan" },
      { key: "output_a", label: "Output A", multiline: true },
      { key: "output_b", label: "Output B", multiline: true },
      { key: "instructions", label: "Judging instructions (optional)", multiline: true, optional: true },
    ],
    run: (v) =>
      runArenaEvaluation({
        query: v.query,
        output_a: v.output_a,
        output_b: v.output_b,
        instructions: v.instructions || undefined,
      }),
  },
  {
    id: "conversational",
    label: "Conversation",
    hint: "Multi-turn compliance scoring across a full back-and-forth transcript.",
    button: "Run conversational evaluation",
    fields: [
      {
        key: "transcript",
        label: "Transcript (one 'role: message' per line)",
        placeholder: "user: Hello\nassistant: Hi!",
        multiline: true,
      },
      { key: "criteria", label: "Criteria (optional)", optional: true },
    ],
    run: (v) =>
      runConversationalEvaluation({
        transcript: lines(v.transcript).map((line) => {
          const separator = line.indexOf(":");
          return separator < 0
            ? { role: "user", content: line }
            : {
                role: line.slice(0, separator).trim(),
                content: line.slice(separator + 1).trim(),
              };
        }),
        criteria: v.criteria || undefined,
      }),
  },
  {
    id: "prompt-regression",
    label: "Prompt gate",
    hint: "Score a candidate system prompt against the guardrail golden set. The gate fails below the threshold.",
    button: "Run prompt-regression gate",
    fields: [
      { key: "system_prompt", label: "System prompt", multiline: true },
      { key: "threshold", label: "Pass threshold (0–1)", type: "number", placeholder: "0.8", optional: true },
    ],
    run: (v) =>
      runPromptRegressionGate({
        system_prompt: v.system_prompt,
        threshold: numberOr(v.threshold, 0.8),
      }),
  },
  {
    id: "redteam",
    label: "Red team",
    hint: "Run the fixed adversarial probe battery against the input guardrails and persist the report.",
    button: "Run red-team probes",
    fields: [],
    run: () => runRedTeamEvaluation(),
  },
  {
    id: "compare",
    label: "Trace compare",
    hint: "Side-by-side cost, token, latency and confidence deltas for two recorded traces.",
    button: "Run trace comparison",
    fields: [
      { key: "trace_a", label: "Trace A id", placeholder: "trace-a" },
      { key: "trace_b", label: "Trace B id", placeholder: "trace-b" },
    ],
    run: (v) => compareEvaluationTraces(v.trace_a, v.trace_b),
  },
  {
    id: "trace",
    label: "Trace detail",
    hint: "Full span for one trace: events, cost, tokens, model and confidence.",
    button: "Load trace",
    fields: [{ key: "trace_id", label: "Trace id", placeholder: "trace-a" }],
    run: (v) => fetchEvaluationTrace(v.trace_id),
  },
];

export const EvaluationPanel: React.FC = () => {
  const [modeId, setModeId] = useState(MODES[0].id);
  const [values, setValues] = useState<Record<string, string>>({});
  const [result, setResult] = useState<unknown>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const mode = MODES.find((candidate) => candidate.id === modeId) ?? MODES[0];
  const missingRequired = mode.fields.some(
    (field) => !field.optional && !(values[field.key] ?? "").trim()
  );

  const selectMode = (id: string) => {
    setModeId(id);
    setValues({});
    setResult(null);
    setError(null);
  };

  const run = async () => {
    setBusy(true);
    setError(null);
    setResult(null);
    try {
      setResult(await mode.run(values));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Evaluation failed");
    } finally {
      setBusy(false);
    }
  };

  const inputClass =
    "w-full rounded-lg border border-[var(--border-subtle)] bg-[var(--bg-main)] px-3 py-2 text-xs text-[var(--text-primary)] placeholder-[var(--text-faint)] outline-none focus:border-[var(--accent)]";

  return (
    <div className="glass-panel space-y-4 p-6">
      <h3 className="flex items-center gap-2 border-b border-[var(--border-subtle)] pb-2 text-sm font-medium text-[var(--text-primary)]">
        <Beaker className="h-4 w-4 text-[var(--accent-ink)]" /> Evaluation studio
      </h3>

      <div className="flex flex-wrap gap-1.5" role="group" aria-label="Evaluator">
        {MODES.map((candidate) => (
          <button
            key={candidate.id}
            type="button"
            onClick={() => selectMode(candidate.id)}
            className={`flex items-center gap-1.5 rounded-lg px-2.5 py-1.5 text-xs font-medium transition-colors ${
              candidate.id === modeId
                ? "bg-[var(--accent-soft)] text-[var(--accent-ink)] border border-[var(--accent)]"
                : "text-[var(--text-muted)] hover:text-[var(--text-secondary)] border border-transparent"
            }`}
          >
            {candidate.id === "compare" ? (
              <GitCompareArrows className="h-3.5 w-3.5" />
            ) : null}
            {candidate.label}
          </button>
        ))}
      </div>

      <p className="text-xs text-[var(--text-muted)]">{mode.hint}</p>

      {mode.fields.length > 0 && (
        <div className="grid gap-3 sm:grid-cols-2">
          {mode.fields.map((field) => (
            <div key={field.key} className={field.multiline ? "sm:col-span-2" : ""}>
              <label
                htmlFor={`eval-${field.key}`}
                className="mb-1.5 block text-xs font-medium text-[var(--text-secondary)]"
              >
                {field.label}
              </label>
              {field.multiline ? (
                <textarea
                  id={`eval-${field.key}`}
                  rows={3}
                  value={values[field.key] ?? ""}
                  placeholder={field.placeholder}
                  onChange={(event) =>
                    setValues((prev) => ({ ...prev, [field.key]: event.target.value }))
                  }
                  className={inputClass}
                />
              ) : (
                <input
                  id={`eval-${field.key}`}
                  type={field.type ?? "text"}
                  value={values[field.key] ?? ""}
                  placeholder={field.placeholder}
                  onChange={(event) =>
                    setValues((prev) => ({ ...prev, [field.key]: event.target.value }))
                  }
                  className={inputClass}
                />
              )}
            </div>
          ))}
        </div>
      )}

      <div className="flex flex-wrap items-center gap-3">
        <button
          type="button"
          onClick={() => void run()}
          disabled={busy || missingRequired}
          className="flex items-center gap-2 rounded-lg bg-[var(--accent)] px-3 py-2 text-xs font-medium text-[var(--accent-foreground)] disabled:opacity-50"
        >
          {busy ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : null}
          {busy ? "Running…" : mode.button}
        </button>
        <span className="text-[11px] text-[var(--text-muted)]">
          Reports are shown verbatim; scored runs are persisted by the backend.
        </span>
      </div>

      {error && (
        <p role="alert" className="text-xs text-[var(--status-danger)]">
          {error}
        </p>
      )}

      {result !== null && (
        <pre
          data-testid="evaluation-result"
          className="max-h-80 overflow-auto rounded-lg border border-[var(--border-subtle)] bg-[var(--bg-main)] p-3 text-[11px] leading-relaxed text-[var(--text-secondary)]"
        >
          {JSON.stringify(result, null, 2)}
        </pre>
      )}
    </div>
  );
};
