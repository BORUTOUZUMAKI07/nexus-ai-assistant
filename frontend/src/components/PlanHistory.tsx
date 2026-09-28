"use client";

import { useState } from "react";
import type { PlanItem } from "@/lib/api";

/**
 * Collapsible history of the plans drafted in the active conversation.
 *
 * This is the read side of the plan-then-approve contract. The draft/approve/
 * reject actions were all wired, but nothing ever loaded the result, so a plan
 * disappeared from the UI the moment it was decided and left no trace a user
 * could come back to — even though the rows were persisted. It also made the
 * "pending" state invisible after a reload, so a plan still awaiting a decision
 * looked like it had never existed.
 *
 * Rendered as a <details> element so it stays out of the way by default: it is
 * reference information, not the primary conversation, and it needs no client
 * state to open and close.
 */

const STATUS_STYLE: Record<PlanItem["status"], { label: string; className: string }> = {
  pending: {
    label: "Awaiting decision",
    className:
      "border-[var(--status-warning)]/40 bg-[var(--status-warning)]/10 text-[var(--status-warning)]",
  },
  approved: {
    label: "Approved",
    className:
      "border-[var(--status-success)]/40 bg-[var(--status-success)]/10 text-[var(--status-success)]",
  },
  rejected: {
    label: "Rejected",
    className:
      "border-[var(--status-danger)]/40 bg-[var(--status-danger)]/10 text-[var(--status-danger)]",
  },
};

function formatDate(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString(undefined, {
    dateStyle: "medium",
    timeStyle: "short",
  });
}

/**
 * The TS type narrows `status` to three values, but the server does not
 * enforce that: PlanDetailResponse declares it as a plain `string` with
 * "pending | approved | rejected" only in the field description. A status the
 * type did not anticipate therefore has no entry, so this falls back rather
 * than dereferencing undefined and blanking the whole panel.
 */
const UNKNOWN_STATUS = {
  label: "Unknown",
  className:
    "border-[var(--border-subtle)] bg-[var(--bg-surface-elevated)] text-[var(--text-muted)]",
} satisfies { label: string; className: string };

function statusStyle(status: string) {
  return STATUS_STYLE[status as PlanItem["status"]] ?? UNKNOWN_STATUS;
}

export default function PlanHistory({ plans }: { plans: PlanItem[] }) {
  const [expandedId, setExpandedId] = useState<string | null>(null);

  // Nothing drafted yet — render nothing rather than an empty disclosure.
  if (plans.length === 0) return null;

  const pendingCount = plans.filter((p) => p.status === "pending").length;

  return (
    <details className="mx-4 mb-2 rounded-lg border border-[var(--border-subtle)] bg-[var(--bg-surface)]">
      <summary className="flex cursor-pointer items-center gap-2 px-3 py-2 text-[11px] font-semibold uppercase tracking-wider text-[var(--text-muted)] select-none hover:text-[var(--text-secondary)]">
        <span className="text-[var(--accent)]">Plans</span>
        <span className="rounded-full border border-[var(--border-subtle)] px-1.5 text-[10px] normal-case tracking-normal text-[var(--text-secondary)]">
          {plans.length}
        </span>
        {pendingCount > 0 && (
          <span className="rounded-full border border-[var(--status-warning)]/40 bg-[var(--status-warning)]/10 px-1.5 text-[10px] normal-case tracking-normal text-[var(--status-warning)]">
            {pendingCount} awaiting decision
          </span>
        )}
      </summary>

      <ul className="max-h-64 overflow-y-auto border-t border-[var(--border-subtle)]">
        {plans.map((plan) => {
          const style = statusStyle(plan.status);
          const isOpen = expandedId === plan.id;
          return (
            <li key={plan.id} className="border-b border-[var(--border-subtle)] last:border-b-0">
              <button
                type="button"
                onClick={() => setExpandedId(isOpen ? null : plan.id)}
                aria-expanded={isOpen}
                className="flex w-full items-center justify-between gap-3 px-3 py-2 text-left hover:bg-[var(--bg-surface-tint)]"
              >
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-[12px] text-[var(--text-primary)]">
                    {plan.title}
                  </span>
                  <span className="block text-[10px] text-[var(--text-faint)]">
                    {plan.steps.length} step{plan.steps.length === 1 ? "" : "s"} ·{" "}
                    {formatDate(plan.created_at)}
                  </span>
                </span>
                <span
                  className={`shrink-0 rounded border px-1.5 py-0.5 text-[10px] ${style.className}`}
                >
                  {style.label}
                </span>
              </button>

              {isOpen && (
                <div className="space-y-2 border-t border-[var(--border-subtle)] bg-[var(--bg-main)]/50 px-3 py-2 text-[11px] text-[var(--text-secondary)]">
                  {plan.summary && <p>{plan.summary}</p>}
                  {plan.steps.length > 0 && (
                    <ol className="list-decimal space-y-0.5 pl-4">
                      {plan.steps.map((step, i) => (
                        <li key={i}>{step}</li>
                      ))}
                    </ol>
                  )}
                  {plan.decision_reason && (
                    <p className="text-[var(--text-faint)]">
                      Decision reason: {plan.decision_reason}
                    </p>
                  )}
                  {plan.decided_at && (
                    <p className="text-[var(--text-faint)]">
                      Decided {formatDate(plan.decided_at)}
                    </p>
                  )}
                </div>
              )}
            </li>
          );
        })}
      </ul>
    </details>
  );
}
