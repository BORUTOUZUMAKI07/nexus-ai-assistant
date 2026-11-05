"use client";

/**
 * Organization invitation landing page.
 *
 * The backend mints invite links as `{APP_PUBLIC_URL}/invite?token=…`, so
 * without this page every invitation an owner sends resolves to a 404 — the
 * email arrives, the recipient clicks, and nothing happens.
 *
 * Two states matter and they are not the same:
 *   - signed out  → the token must survive the trip through sign-in, so the
 *                  sign-in link carries it as `?next=`;
 *   - signed in   → redeem immediately, because a page that asks the recipient
 *                  to click "accept" a second time after they already clicked
 *                  the link in their email is a step with no purpose.
 *
 * The token is read from `window.location.search` via `useSyncExternalStore`
 * rather than `useSearchParams`, matching the sign-in page: the server has no
 * query string, so the server snapshot is empty and React swaps in the real
 * value on the client without a hydration mismatch or a Suspense boundary.
 */

import React, { useEffect, useState, useSyncExternalStore } from "react";
import Link from "next/link";
import { AlertCircle, CheckCircle2, Loader2, MailCheck } from "lucide-react";
import { acceptOrganizationInvite } from "@/lib/api";
import { checkAuth } from "@/lib/auth";

const SUBSCRIBE = () => () => {};
const getServerSnapshot = () => "";
const getSnapshot = () => window.location.search;

type Phase =
  | { kind: "resolving" }
  | { kind: "signed-out" }
  | { kind: "accepting" }
  | { kind: "joined"; name: string | null }
  | { kind: "failed"; message: string };

export default function InvitePage() {
  const search = useSyncExternalStore(SUBSCRIBE, getSnapshot, getServerSnapshot);
  const token = new URLSearchParams(search).get("token");
  // What the recipient is trying to get to once authenticated. Built from the
  // raw search string so a second query parameter (a workspace hint, a locale)
  // survives instead of being flattened into just the token.
  const signInHref = `/signin?next=${encodeURIComponent(`/invite${search}`)}`;

  const [phase, setPhase] = useState<Phase>({ kind: "resolving" });
  // Derived, not stored: a link with no token is knowable during render, so
  // putting it in state would mean a setState in an effect for a value that
  // cannot change, and a flash of "checking" before the obvious answer.
  const missingToken = search !== "" && !token;

  useEffect(() => {
    // An empty string means "the server rendered this" — there is no token to
    // judge yet, and guessing would flash the wrong screen on every load.
    if (!search || !token) return;

    let cancelled = false;
    void (async () => {
      const authed = await checkAuth();
      if (cancelled) return;
      if (!authed) {
        setPhase({ kind: "signed-out" });
        return;
      }
      setPhase({ kind: "accepting" });
      try {
        const org = await acceptOrganizationInvite(token);
        if (!cancelled) setPhase({ kind: "joined", name: org?.name ?? null });
      } catch (err) {
        if (!cancelled) {
          setPhase({
            kind: "failed",
            message:
              err instanceof Error ? err.message : "This invite could not be accepted.",
          });
        }
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [search, token]);

  return (
    <div className="flex min-h-screen items-center justify-center bg-[var(--bg-main)] px-4">
      <div className="w-full max-w-md">
        <div className="mb-8 flex flex-col items-center text-center">
          <Link href="/" className="flex items-center justify-center">
            <img src="/nexus-logo.svg" alt="Nexus" className="h-14 w-14 rounded-xl shadow-sm" />
          </Link>
          <h1 className="mt-5 text-2xl font-semibold tracking-tight text-[var(--text-primary)]">
            Organization invitation
          </h1>
        </div>

        <div className="rounded-xl border border-[var(--border-subtle)] bg-[var(--bg-surface)] p-6 shadow-float">
          {!missingToken && (phase.kind === "resolving" || phase.kind === "accepting") && (
            <p className="flex items-center gap-2 text-sm text-[var(--text-muted)]">
              <Loader2 className="h-4 w-4 animate-spin" />
              {phase.kind === "accepting" ? "Joining the organization…" : "Checking your session…"}
            </p>
          )}

          {phase.kind === "signed-out" && (
            <div className="space-y-4">
              <p className="flex items-center gap-2 text-sm text-[var(--text-secondary)]">
                <MailCheck className="h-4 w-4 shrink-0" />
                Sign in to accept this invitation. The link will bring you straight back here.
              </p>
              <Link
                href={signInHref}
                className="flex w-full items-center justify-center rounded-lg bg-[var(--text-primary)] px-4 py-2.5 text-sm font-medium text-[var(--bg-main)]"
              >
                Sign in to accept
              </Link>
            </div>
          )}

          {phase.kind === "joined" && (
            <div className="space-y-4">
              <p className="flex items-center gap-2 text-sm text-[var(--text-primary)]">
                <CheckCircle2 className="h-4 w-4 shrink-0 text-[var(--status-success)]" />
                {phase.name ? `You've joined ${phase.name}.` : "You've joined the organization."}
              </p>
              <Link
                href="/app"
                className="flex w-full items-center justify-center rounded-lg bg-[var(--text-primary)] px-4 py-2.5 text-sm font-medium text-[var(--bg-main)]"
              >
                Open Nexus
              </Link>
            </div>
          )}

          {(phase.kind === "failed" || missingToken) && (
            <div className="space-y-4">
              <p className="flex items-start gap-2 text-sm text-[var(--text-primary)]">
                <AlertCircle className="mt-0.5 h-4 w-4 shrink-0 text-[var(--status-danger)]" />
                <span>
                  {missingToken || phase.kind !== "failed"
                    ? "This invitation link is missing its token. Ask the person who invited you to send it again."
                    : phase.message}
                </span>
              </p>
              <Link
                href="/app"
                className="flex w-full items-center justify-center rounded-lg border border-[var(--border-subtle)] px-4 py-2.5 text-sm font-medium text-[var(--text-secondary)]"
              >
                Go to Nexus
              </Link>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
