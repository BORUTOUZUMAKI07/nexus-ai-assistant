"use client";

import { TriangleAlert } from "lucide-react";

/**
 * Shared fallback UI for the App Router `error.tsx` boundaries.
 *
 * Renders inside the root layout, so it can use the theme tokens from
 * globals.css (Void background, glass-card surface, Electric Acid Lime accent).
 * `digest` is the route-error identifier Next generates for server-log
 * correlation; it is shown as muted monospace copy and never its message.
 */
export function AppErrorState({
  title = "Something went wrong",
  description = "An unexpected error occurred while rendering this view. Your data is safe — try again, or head back to start.",
  digest,
  onRetry,
}: {
  title?: string;
  description?: string;
  digest?: string;
  onRetry?: () => void;
}) {
  return (
    <div className="flex min-h-screen items-center justify-center px-6">
      <div className="glass-card w-full max-w-md px-8 py-10 text-center">
        <div className="mx-auto mb-5 flex h-12 w-12 items-center justify-center rounded-full border border-[var(--border-subtle)] bg-[var(--bg-surface-elevated)]">
          <TriangleAlert
            className="h-5 w-5 text-[var(--status-danger)]"
            aria-hidden="true"
          />
        </div>
        <h1 className="text-lg font-semibold text-[var(--text-primary)]">
          {title}
        </h1>
        <p className="mt-2 text-sm leading-relaxed text-[var(--text-muted)]">
          {description}
        </p>
        {digest ? (
          <p className="mt-3 font-mono text-xs text-[var(--text-faint)]">
            digest: {digest}
          </p>
        ) : null}
        {onRetry ? (
          <button
            type="button"
            onClick={onRetry}
            className="btn-electric mt-6 px-5 py-2 text-sm"
          >
            Try again
          </button>
        ) : null}
      </div>
    </div>
  );
}

export default AppErrorState;