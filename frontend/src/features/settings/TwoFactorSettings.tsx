"use client";

import React, { useEffect, useState } from "react";
import { Loader2, ShieldCheck } from "lucide-react";
import {
  disableTwoFactor,
  enableTwoFactor,
  getTwoFactorStatus,
  setupTwoFactor,
  type TwoFactorSetup,
} from "@/lib/api";

/**
 * TOTP enrollment panel.
 *
 * The backend owns the secret and the code verification; this component only
 * walks the user through the three-step contract (status -> setup -> confirm)
 * and makes sure a half-configured enrollment is never left looking "enabled".
 */
export const TwoFactorSettings: React.FC = () => {
  const [enabled, setEnabled] = useState<boolean | null>(null);
  const [setup, setSetup] = useState<TwoFactorSetup | null>(null);
  const [code, setCode] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    void getTwoFactorStatus()
      .then((status) => {
        if (active) setEnabled(status.enabled);
      })
      .catch((err: unknown) => {
        if (active) {
          setError(err instanceof Error ? err.message : "Could not load two-factor status");
        }
      });
    return () => {
      active = false;
    };
  }, []);

  const messageFor = (fallback: string, err: unknown) =>
    err instanceof Error && err.message ? err.message : fallback;

  const handleStart = async () => {
    setBusy(true);
    setError(null);
    try {
      setSetup(await setupTwoFactor());
    } catch (err) {
      setError(messageFor("Could not start two-factor enrollment", err));
    } finally {
      setBusy(false);
    }
  };

  const handleEnable = async () => {
    if (!code.trim()) return;
    setBusy(true);
    setError(null);
    try {
      await enableTwoFactor(code.trim());
      setEnabled(true);
      setSetup(null);
      setCode("");
    } catch (err) {
      setError(messageFor("Could not enable two-factor authentication", err));
    } finally {
      setBusy(false);
    }
  };

  const handleDisable = async () => {
    if (!code.trim()) return;
    setBusy(true);
    setError(null);
    try {
      await disableTwoFactor(code.trim());
      setEnabled(false);
      setCode("");
    } catch (err) {
      setError(messageFor("Could not disable two-factor authentication", err));
    } finally {
      setBusy(false);
    }
  };

  const codeInput = (help: string) => (
    <div>
      <label
        htmlFor="two-factor-code"
        className="mb-1.5 block text-xs font-medium text-[var(--text-secondary)]"
      >
        Authentication code
      </label>
      <input
        id="two-factor-code"
        value={code}
        onChange={(event) => setCode(event.target.value)}
        inputMode="numeric"
        autoComplete="one-time-code"
        maxLength={10}
        placeholder="000000"
        className="w-full rounded-lg border border-[var(--border-subtle)] bg-[var(--bg-main)] px-3 py-2 text-xs tracking-[0.3em] text-[var(--text-primary)] outline-none focus:border-[var(--accent)]"
      />
      <p className="mt-1 text-[11px] text-[var(--text-muted)]">{help}</p>
    </div>
  );

  return (
    <div className="glass-panel p-6 space-y-4">
      <h3 className="text-sm font-medium text-[var(--text-primary)] flex items-center gap-2 border-b border-[var(--border-subtle)] pb-2">
        <ShieldCheck className="w-4 h-4 text-[var(--accent-ink)]" /> Two-factor authentication
      </h3>

      {error && (
        <p role="alert" className="text-xs text-[var(--status-danger)]">
          {error}
        </p>
      )}

      {enabled === null ? (
        <p className="text-xs text-[var(--text-muted)]">Checking two-factor status…</p>
      ) : (
        <>
          <p className="text-xs text-[var(--text-muted)]">
            {enabled
              ? "Two-factor authentication is enabled. Your account asks for a one-time code at sign-in."
              : "Two-factor authentication is disabled. Add a TOTP app to protect your account."}
          </p>

          {!enabled && !setup && (
            <button
              type="button"
              onClick={() => void handleStart()}
              disabled={busy}
              className="flex items-center gap-2 rounded-lg bg-[var(--accent)] px-3 py-2 text-xs font-medium text-[var(--accent-foreground)] disabled:opacity-50"
            >
              {busy ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <ShieldCheck className="w-3.5 h-3.5" />}
              Enable two-factor authentication
            </button>
          )}

          {!enabled && setup && (
            <div className="space-y-3 rounded-lg border border-[var(--border-subtle)] bg-[var(--bg-main)] p-4">
              <p className="text-xs text-[var(--text-secondary)]">
                Add this secret to your authenticator app, then confirm with the code it shows.
              </p>
              <code className="block break-all rounded bg-[var(--bg-surface)] px-3 py-2 font-mono text-xs text-[var(--accent-ink)]">
                {setup.secret}
              </code>
              <a
                href={setup.otpauth_uri}
                className="inline-block text-xs text-[var(--accent-ink)] underline underline-offset-4"
              >
                Open in authenticator app
              </a>
              {codeInput("One code confirms enrollment and is never accepted twice.")}
              <button
                type="button"
                onClick={() => void handleEnable()}
                disabled={busy || !code.trim()}
                className="flex items-center gap-2 rounded-lg bg-[var(--accent)] px-3 py-2 text-xs font-medium text-[var(--accent-foreground)] disabled:opacity-50"
              >
                {busy ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : null}
                Confirm and enable
              </button>
            </div>
          )}

          {enabled && (
            <div className="space-y-3 rounded-lg border border-[var(--border-subtle)] bg-[var(--bg-main)] p-4">
              {codeInput("Enter a current code from your authenticator app to turn 2FA off.")}
              <button
                type="button"
                onClick={() => void handleDisable()}
                disabled={busy || !code.trim()}
                className="flex items-center gap-2 rounded-lg border border-[var(--status-danger)] px-3 py-2 text-xs font-medium text-[var(--status-danger)] disabled:opacity-50"
              >
                {busy ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : null}
                Disable two-factor authentication
              </button>
            </div>
          )}
        </>
      )}
    </div>
  );
};
