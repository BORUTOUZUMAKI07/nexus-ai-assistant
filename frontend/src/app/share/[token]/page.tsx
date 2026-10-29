"use client";

/**
 * Public, read-only view of a shared conversation.
 *
 * This is the page the backend's share URL points at
 * (`{APP_PUBLIC_URL}/share/{token}`), so without it every share link the API
 * hands out resolves to a 404. It talks only to the BFF's unauthenticated
 * `/api/public/shares/{token}` route: no session is required, and nothing but
 * the conversation's own content and public-safe citations is exposed.
 */

import React, { useEffect, useState } from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { FileWarning, Loader2, MessageSquare } from "lucide-react";
import { MarkdownRenderer } from "@/components/ChatArea";

type SharedMessage = {
  role: string;
  content: string;
  model?: string | null;
  citations?: unknown;
  created_at?: string | null;
};

type SharePayload = {
  conversation: { id: string; title: string; created_at?: string | null };
  messages: SharedMessage[];
};

export default function SharedConversationPage() {
  const params = useParams<{ token: string }>();
  const token = params?.token ?? "";

  const [payload, setPayload] = useState<SharePayload | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    if (!token) return;
    let cancelled = false;
    (async () => {
      try {
        const res = await fetch(`/api/public/shares/${encodeURIComponent(token)}`);
        const text = await res.text();
        const data = text ? (JSON.parse(text) as SharePayload & { detail?: string }) : null;
        if (!res.ok || !data?.conversation) {
          throw new Error(
            typeof data?.detail === "string"
              ? data.detail
              : "This share link is invalid or has expired."
          );
        }
        if (!cancelled) setPayload(data);
      } catch (err) {
        if (!cancelled) {
          setError(
            err instanceof Error
              ? err.message
              : "This share link is invalid or has expired."
          );
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [token]);

  return (
    <div className="min-h-screen bg-[var(--bg-main)] text-[var(--text-primary)]">
      <header className="border-b border-[var(--border-subtle)]">
        <div className="max-w-3xl mx-auto px-6 py-4 flex items-center justify-between">
          <div className="flex items-center gap-2.5">
            <img src="/nexus-logo.svg" alt="Nexus" className="w-7 h-7 rounded-lg" />
            <span className="font-semibold text-sm tracking-tight">
              {payload?.conversation.title ?? "Shared conversation"}
            </span>
          </div>
          <Link
            href="/signin"
            className="text-xs text-[var(--text-muted)] hover:text-[var(--text-primary)]"
          >
            Open Nexus
          </Link>
        </div>
      </header>

      <main className="max-w-3xl mx-auto px-6 py-8 space-y-5">
        <p className="flex items-center gap-1.5 text-[11px] uppercase tracking-wider text-[var(--text-faint)]">
          <MessageSquare className="w-3.5 h-3.5" /> Read-only shared transcript
        </p>

        {loading && (
          <p className="flex items-center gap-2 text-sm text-[var(--text-muted)]">
            <Loader2 className="w-4 h-4 animate-spin" /> Loading conversation…
          </p>
        )}

        {error && (
          <div className="p-4 rounded-lg border border-[var(--status-danger)]/40 bg-[var(--status-danger)]/10 text-sm flex items-start gap-2">
            <FileWarning className="w-4 h-4 mt-0.5 shrink-0 text-[var(--status-danger)]" />
            <span>{error}</span>
          </div>
        )}

        {payload?.messages.map((m, i) => (
          <article
            key={`${m.role}-${i}`}
            className={
              m.role === "user"
                ? "ml-8 p-3 rounded-lg border border-[var(--border-subtle)] bg-[var(--bg-surface)]"
                : "mr-4 p-4 rounded-lg border border-[var(--border-subtle)] bg-[var(--bg-surface-elevated)]"
            }
          >
            <p className="text-[10px] uppercase tracking-wider text-[var(--text-faint)] mb-2">
              {m.role === "user" ? "You" : "Nexus"}
              {m.model ? ` · ${m.model}` : ""}
            </p>
            <div className="text-sm leading-relaxed">
              {m.role === "user" ? (
                <p className="whitespace-pre-wrap">{m.content}</p>
              ) : (
                <MarkdownRenderer content={m.content} />
              )}
            </div>
          </article>
        ))}

        {payload && payload.messages.length === 0 && (
          <p className="text-sm text-[var(--text-muted)]">
            This conversation has no messages yet.
          </p>
        )}
      </main>
    </div>
  );
}