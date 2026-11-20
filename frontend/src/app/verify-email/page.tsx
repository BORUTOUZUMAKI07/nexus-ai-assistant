"use client";

/**
 * Landing page for the token in the verification email.
 *
 * The backend mints `{APP_PUBLIC_URL}/verify-email?token=…`. Without this page
 * the link 404s, so enabling EMAIL_VERIFICATION_REQUIRED would strand every
 * new account. The endpoint is public: no session is needed to redeem it.
 */
import React, { useEffect, useRef, useState, useSyncExternalStore } from "react";
import Link from "next/link";
import Image from "next/image";
import { AlertCircle, CheckCircle2, Loader2 } from "lucide-react";
import { verifyEmailToken } from "@/lib/api";

const SUBSCRIBE = () => () => {};
const getServerSnapshot = () => "";
const getSnapshot = () => window.location.search;

type Phase =
  | { kind: "verifying" }
  | { kind: "verified" }
  | { kind: "failed"; message: string };

export default function VerifyEmailPage() {
  const search = useSyncExternalStore(SUBSCRIBE, getSnapshot, getServerSnapshot);
  const token = new URLSearchParams(search).get("token");
  const [phase, setPhase] = useState<Phase>({ kind: "verifying" });
  const started = useRef(false);
  const missingToken = search !== "" && !token;

  useEffect(() => {
    if (!search || !token || started.current) return;
    started.current = true;
    let cancelled = false;
    void (async () => {
      try {
        await verifyEmailToken(token);
        if (!cancelled) setPhase({ kind: "verified" });
      } catch (err) {
        if (!cancelled) {
          setPhase({
            kind: "failed",
            message: err instanceof Error ? err.message : "This link could not be verified.",
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
            <Image
              src="/nexus-logo.svg"
              alt="Nexus"
              width={56}
              height={56}
              className="h-14 w-14 rounded-xl shadow-sm"
            />
          </Link>
          <h1 className="mt-5 text-2xl font-semibold tracking-tight text-[var(--text-primary)]">
            Email verification
          </h1>
        </div>

        <div className="rounded-xl border border-[var(--border-subtle)] bg-[var(--bg-surface)] p-6 shadow-float">
          {!missingToken && phase.kind === "verifying" && (
            <p className="flex items-center gap-2 text-sm text-[var(--text-muted)]">
              <Loader2 className="h-4 w-4 animate-spin" />
              Verifying your email…
            </p>
          )}

          {phase.kind === "verified" && (
            <div className="space-y-4">
              <p className="flex items-center gap-2 text-sm text-[var(--text-primary)]">
                <CheckCircle2 className="h-4 w-4 text-[var(--status-success)]" />
                Email verified. You can sign in now.
              </p>
              <Link
                href="/signin"
                className="flex w-full items-center justify-center rounded-lg bg-[var(--text-primary)] px-4 py-2.5 text-sm font-medium text-[var(--bg-main)]"
              >
                Sign in
              </Link>
            </div>
          )}

          {(phase.kind === "failed" || missingToken) && (
            <div role="alert" className="space-y-4">
              <p className="flex items-start gap-2 text-sm text-[var(--text-primary)]">
                <AlertCircle className="mt-0.5 h-4 w-4 shrink-0 text-[var(--status-danger)]" />
                <span>
                  {phase.kind === "failed"
                    ? phase.message
                    : "This verification link is missing its token. Ask for a new verification email."}
                </span>
              </p>
              <Link
                href="/signin"
                className="flex w-full items-center justify-center rounded-lg border border-[var(--border-subtle)] px-4 py-2.5 text-sm font-medium text-[var(--text-secondary)]"
              >
                Back to sign in
              </Link>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
