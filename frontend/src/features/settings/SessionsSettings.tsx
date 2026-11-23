"use client";

import React, { useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { Laptop, Loader2, Shield } from "lucide-react";
import { revokeSession, type SessionInfo } from "@/lib/api";
import { settingsKeys, useSessionsQuery } from "@/lib/queries";

/**
 * Summarise a user-agent into a device label. The full string stays in the
 * tooltip below; a settings row cannot show 200 characters of browser tokens
 * without destroying the scanability of the list.
 */
export function deviceLabel(userAgent: string): string {
  const ua = userAgent || "";
  const os = /Windows/.test(ua)
    ? "Windows"
    : /iPad/.test(ua)
      ? "iPad"
      : /iPhone|iOS/.test(ua)
        ? "iPhone"
        : /Android/.test(ua)
          ? "Android"
          : /Mac OS X|Macintosh/.test(ua)
            ? "macOS"
            : /Linux/.test(ua)
              ? "Linux"
              : "";
  const browser = /Edg\//.test(ua)
    ? "Edge"
    : /Firefox\//.test(ua)
      ? "Firefox"
      : /Chrome\//.test(ua)
        ? "Chrome"
        : /Safari\//.test(ua)
          ? "Safari"
          : "";
  if (browser && os) return `${browser} on ${os}`;
  return browser || os || "Unknown device";
}

function formatInstant(value: string | number): string {
  const date =
    typeof value === "number" ? new Date(value * 1000) : new Date(value);
  return Number.isNaN(date.getTime())
    ? "—"
    : date.toLocaleString(undefined, {
        year: "numeric",
        month: "short",
        day: "numeric",
        hour: "2-digit",
        minute: "2-digit",
      });
}

/**
 * Active sign-ins and per-device revocation.
 *
 * The inventory is a best-effort index, not an authorization source: the
 * backend scopes each family id to the caller and writes the real revocation
 * marker. This panel therefore removes a row only after the server confirms.
 * The current sign-in has no revoke control — Sign out is the correct way to
 * end it, and revoking it here would leave this tab holding a still-valid
 * access cookie with no obvious next step.
 */
export const SessionsSettings: React.FC = () => {
  const queryClient = useQueryClient();
  const sessionsQuery = useSessionsQuery();
  const [revoking, setRevoking] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const sessions = sessionsQuery.data ?? [];

  const handleRevoke = async (session: SessionInfo) => {
    if (session.is_current || revoking) return;
    const label = deviceLabel(session.user_agent);
    if (!window.confirm(`Sign out ${label}? Its access is ended immediately.`)) {
      return;
    }
    setRevoking(session.family_id);
    setError(null);
    try {
      await revokeSession(session.family_id);
      queryClient.setQueryData<SessionInfo[]>(settingsKeys.sessions, (prev) =>
        (prev ?? []).filter((s) => s.family_id !== session.family_id),
      );
    } catch (err) {
      setError(
        err instanceof Error && err.message
          ? err.message
          : "Could not revoke session",
      );
    } finally {
      setRevoking(null);
    }
  };

  return (
    <div className="glass-panel p-6 space-y-4">
      <h3 className="text-sm font-medium text-[var(--text-primary)] flex items-center gap-2 border-b border-[var(--border-subtle)] pb-2">
        <Shield className="w-4 h-4 text-[var(--accent-ink)]" /> Active sessions
      </h3>

      {error && (
        <p role="alert" className="text-xs text-[var(--status-danger)]">
          {error}
        </p>
      )}

      {sessionsQuery.isPending ? (
        <p className="text-xs text-[var(--text-muted)]">Loading sessions…</p>
      ) : sessionsQuery.isError ? (
        <p role="alert" className="text-xs text-[var(--status-danger)]">
          {sessionsQuery.error instanceof Error
            ? sessionsQuery.error.message
            : "Could not load sessions"}
        </p>
      ) : sessions.length === 0 ? (
        <p className="text-xs text-[var(--text-muted)]">
          No active sessions are recorded yet. They appear here after you sign
          in.
        </p>
      ) : (
        <ul className="space-y-2">
          {sessions.map((session) => (
            <li
              key={session.family_id}
              className="flex items-center justify-between gap-3 rounded-lg border border-[var(--border-subtle)] bg-[var(--bg-main)] px-3 py-2 text-xs"
            >
              <div className="min-w-0">
                <div className="flex flex-wrap items-center gap-2">
                  <Laptop
                    className="h-3.5 w-3.5 shrink-0 text-[var(--text-faint)]"
                    aria-hidden="true"
                  />
                  <span className="font-medium text-[var(--text-primary)]">
                    {deviceLabel(session.user_agent)}
                  </span>
                  {session.is_current && (
                    <span className="rounded-full border border-[var(--accent)]/30 bg-[var(--accent-soft)] px-2 py-0.5 text-[10px] text-[var(--accent-ink)]">
                      This device
                    </span>
                  )}
                </div>
                <p
                  className="mt-1 truncate text-[11px] text-[var(--text-muted)]"
                  title={session.user_agent}
                >
                  Signed in {formatInstant(session.created_at)} · Last seen{" "}
                  {formatInstant(session.last_seen)} · Expires{" "}
                  {formatInstant(session.expires_at)}
                </p>
              </div>
              {!session.is_current && (
                <button
                  type="button"
                  onClick={() => void handleRevoke(session)}
                  disabled={revoking === session.family_id}
                  className="flex shrink-0 items-center gap-1.5 rounded-lg border border-[var(--status-danger)] px-3 py-1.5 text-[11px] font-medium text-[var(--status-danger)] transition-colors hover:bg-[var(--status-danger)]/10 disabled:opacity-50"
                >
                  {revoking === session.family_id ? (
                    <Loader2 className="h-3 w-3 animate-spin" aria-hidden="true" />
                  ) : null}
                  Revoke
                </button>
              )}
            </li>
          ))}
        </ul>
      )}

      <p className="text-[11px] text-[var(--text-muted)]">
        Revoking ends that sign-in immediately. Use Sign out to end your current
        session.
      </p>
    </div>
  );
};
