"use client";

import React, { useState, useEffect } from "react";
import { X, Lock, Mail, User, ArrowRight, Sparkles, Loader2 } from "lucide-react";
import { registerUser, loginUser, startOAuthLogin, listOAuthProviders, type OAuthProviderName } from "@/lib/api";
import {
  Dialog,
  DialogContent,
  DialogTitle,
  DialogDescription,
} from "@/components/ui/dialog";

interface AuthModalProps {
  isOpen: boolean;
  onClose: () => void;
  onSuccess: () => void;
}

export const AuthModal: React.FC<AuthModalProps> = ({ isOpen, onClose, onSuccess }) => {
  const [mode, setMode] = useState<"login" | "register">("login");
  const [email, setEmail] = useState("");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [fullName, setFullName] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [oauthProvider, setOAuthProvider] = useState<OAuthProviderName | null>(null);
  // Empty until the server says otherwise: showing the buttons optimistically
  // and hiding them a moment later is the wrong direction to flicker, and a
  // deployment with no OAuth configured would offer a button that 404s.
  const [oauthProviders, setOAuthProviders] = useState<OAuthProviderName[]>([]);

  useEffect(() => {
    let live = true;
    void listOAuthProviders()
      .then((providers) => {
        if (!live) return;
        const usable = providers
          .filter((p) => p.configured)
          .map((p) => p.name)
          .filter((name): name is OAuthProviderName => name === "google" || name === "github");
        // Unconditional, including the empty case: "the server reports none
        // configured" must hide the section, not fall back to a hardcoded pair.
        setOAuthProviders(usable);
      })
      .catch(() => {
        // `listOAuthProviders` resolves to [] on the failures the app itself
        // produces; a throw here is a programming error. Stay hidden either way.
        if (live) setOAuthProviders([]);
      });
    return () => {
      live = false;
    };
  }, []);

  const handleOAuth = async (provider: OAuthProviderName) => {
    setError(null);
    setOAuthProvider(provider);
    try {
      const { authorization_url } = await startOAuthLogin(provider);
      window.location.assign(authorization_url);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Single sign-on is unavailable.");
      setOAuthProvider(null);
    }
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    setLoading(true);

    try {
      if (mode === "register") {
        await registerUser({
          email,
          username: username || email.split("@")[0],
          password,
          full_name: fullName,
        });
      }
      // The /api/auth/login route handler sets the httpOnly session cookies;
      // on success we just close the modal — no tokens touch page scripts.
      await loginUser({ email, password });
      onSuccess();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Authentication failed. Please check your credentials.");
    } finally {
      setLoading(false);
    }
  };

  return (
    <Dialog open={isOpen} onOpenChange={(open) => { if (!open) onClose(); }}>
      <DialogContent
        // Sign-in is a deliberate flow: close via the X or Escape, never by a stray outside click.
        onPointerDownOutside={(e) => e.preventDefault()}
        className="border-[var(--border-strong)] bg-[var(--bg-surface)]"
      >
        {/* Close Button */}
        <button
          type="button"
          onClick={onClose}
          aria-label="Close"
          className="absolute right-4 top-4 rounded-lg p-1.5 text-[var(--text-faint)] hover:bg-[var(--bg-surface-tint)] hover:text-[var(--text-secondary)] transition-colors"
        >
          <X className="h-5 w-5" />
        </button>

        {/* Header */}
        <div className="flex items-center gap-3 mb-6">
          <div className="flex h-10 w-10 items-center justify-center rounded-lg border border-[var(--border-subtle)] bg-[var(--bg-main)] text-[var(--accent-ink)]">
            <Sparkles className="h-5 w-5" />
          </div>
          <div>
            <DialogTitle className="text-xl font-semibold tracking-tight text-[var(--text-primary)]">
              {mode === "login" ? "Welcome back" : "Create your account"}
            </DialogTitle>
            <DialogDescription className="text-xs text-[var(--text-muted)]">
              {mode === "login"
                ? "Sign in to access your Nexus assistant"
                : "Join Nexus for production AI workflows"}
            </DialogDescription>
          </div>
        </div>

        {error && (
          <div className="mb-4 rounded-lg border border-[var(--border-strong)] bg-[var(--bg-main)] p-3 text-xs text-[var(--status-danger)]">
            {error}
          </div>
        )}

        {/* Form */}
        <form onSubmit={handleSubmit} className="space-y-4">
          {mode === "register" && (
            <>
              <div>
                <label className="block text-xs font-medium text-[var(--text-secondary)] mb-1.5" htmlFor="auth-fullname">Full Name</label>
                <div className="relative">
                  <User className="absolute left-3 top-2.5 h-4 w-4 text-[var(--text-faint)]" />
                  <input
                    type="text"
                    required
                    value={fullName}
                    onChange={(e) => setFullName(e.target.value)}
                    id="auth-fullname"
                    placeholder="Jane Doe"
                    className="w-full rounded-lg bg-[var(--bg-main)] border border-[var(--border-subtle)] pl-9 pr-4 py-2 text-sm text-[var(--text-primary)] placeholder-[var(--text-faint)] focus:border-[var(--accent)] focus:outline-none focus:ring-1 focus:ring-[var(--accent)]"
                  />
                </div>
              </div>
              <div>
                <label className="block text-xs font-medium text-[var(--text-secondary)] mb-1.5" htmlFor="auth-username">Username</label>
                <div className="relative">
                  <User className="absolute left-3 top-2.5 h-4 w-4 text-[var(--text-faint)]" />
                  <input
                    type="text"
                    required
                    value={username}
                    onChange={(e) => setUsername(e.target.value)}
                    id="auth-username"
                    placeholder="janedoe"
                    className="w-full rounded-lg bg-[var(--bg-main)] border border-[var(--border-subtle)] pl-9 pr-4 py-2 text-sm text-[var(--text-primary)] placeholder-[var(--text-faint)] focus:border-[var(--accent)] focus:outline-none focus:ring-1 focus:ring-[var(--accent)]"
                  />
                </div>
              </div>
            </>
          )}

          <div>
            <label className="block text-xs font-medium text-[var(--text-secondary)] mb-1.5" htmlFor="auth-email">Email address</label>
            <div className="relative">
              <Mail className="absolute left-3 top-2.5 h-4 w-4 text-[var(--text-faint)]" />
              <input
                type="email"
                required
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                id="auth-email"
                    placeholder="name@example.com"
                className="w-full rounded-lg bg-[var(--bg-main)] border border-[var(--border-subtle)] pl-9 pr-4 py-2 text-sm text-[var(--text-primary)] placeholder-[var(--text-faint)] focus:border-[var(--accent)] focus:outline-none focus:ring-1 focus:ring-[var(--accent)]"
              />
            </div>
          </div>

          <div>
            <label className="block text-xs font-medium text-[var(--text-secondary)] mb-1.5" htmlFor="auth-password">Password</label>
            <div className="relative">
              <Lock className="absolute left-3 top-2.5 h-4 w-4 text-[var(--text-faint)]" />
              <input
                type="password"
                required
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                id="auth-password"
                    placeholder="••••••••"
                className="w-full rounded-lg bg-[var(--bg-main)] border border-[var(--border-subtle)] pl-9 pr-4 py-2 text-sm text-[var(--text-primary)] placeholder-[var(--text-faint)] focus:border-[var(--accent)] focus:outline-none focus:ring-1 focus:ring-[var(--accent)]"
              />
            </div>
          </div>

          <button
            type="submit"
            disabled={loading}
            className="w-full mt-2 flex items-center justify-center gap-2 rounded-lg bg-[var(--accent)] hover:bg-[var(--accent-hover)] py-2.5 text-sm font-semibold text-[var(--accent-foreground)] transition-all disabled:opacity-50 shadow-sm"
          >
            {loading ? (
              <Loader2 className="h-4 w-4 animate-spin" />
            ) : (
              <>
                {mode === "login" ? "Sign In" : "Get Started"}
                <ArrowRight className="h-4 w-4" />
              </>
            )}
          </button>
        </form>

        {oauthProviders.length > 0 && (
          <>
            <div className="mt-4 flex items-center gap-3 text-xs text-[var(--text-faint)]">
              <span className="h-px flex-1 bg-[var(--border-subtle)]" />
              or continue with
              <span className="h-px flex-1 bg-[var(--border-subtle)]" />
            </div>

            <div className="mt-3 grid gap-2">
              {oauthProviders.includes("google") && (
                <button
                  type="button"
                  onClick={() => handleOAuth("google")}
                  disabled={oauthProvider !== null}
                  className="flex w-full items-center justify-center gap-2 rounded-lg border border-[var(--border-subtle)] bg-[var(--bg-main)] px-4 py-2.5 text-sm font-medium text-[var(--text-secondary)] shadow-sm transition-all hover:border-[var(--accent)] hover:text-[var(--accent-ink)] disabled:opacity-50"
                >
                  {oauthProvider === "google" ? <Loader2 className="h-4 w-4 animate-spin" /> : <GoogleGlyph />}
                  {oauthProvider === "google" ? "Redirecting…" : "Continue with Google"}
                </button>
              )}
              {oauthProviders.includes("github") && (
                <button
                  type="button"
                  onClick={() => handleOAuth("github")}
                  disabled={oauthProvider !== null}
                  className="flex w-full items-center justify-center gap-2 rounded-lg border border-[var(--border-subtle)] bg-[var(--bg-main)] px-4 py-2.5 text-sm font-medium text-[var(--text-secondary)] shadow-sm transition-all hover:border-[var(--accent)] hover:text-[var(--accent-ink)] disabled:opacity-50"
                >
                  {oauthProvider === "github" ? <Loader2 className="h-4 w-4 animate-spin" /> : <GitHubGlyph />}
                  {oauthProvider === "github" ? "Redirecting…" : "Continue with GitHub"}
                </button>
              )}
            </div>
          </>
        )}

        {/* Footer Toggle */}
        <div className="mt-6 pt-4 border-t border-[var(--border-subtle)] text-center text-xs text-[var(--text-muted)]">
          {mode === "login" ? (
            <span>
              Don&apos;t have an account?{" "}
              <button
                type="button"
                onClick={() => setMode("register")}
                className="font-medium text-[var(--accent-ink)] hover:text-[var(--accent-ink)] hover:underline"
              >
                Sign up
              </button>
            </span>
          ) : (
            <span>
              Already have an account?{" "}
              <button
                type="button"
                onClick={() => setMode("login")}
                className="font-medium text-[var(--accent-ink)] hover:text-[var(--accent-ink)] hover:underline"
              >
                Sign in
              </button>
            </span>
          )}
        </div>
      </DialogContent>
    </Dialog>
  );
};

function GoogleGlyph() {
  return (
    <svg className="h-4 w-4" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
      <path d="M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92a5.06 5.06 0 0 1-2.2 3.32v2.77h3.57c2.08-1.92 3.28-4.74 3.28-8.1z" />
      <path d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.57-2.77c-.98.66-2.23 1.06-3.71 1.06-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84C3.99 20.53 7.7 23 12 23z" />
      <path d="M5.84 14.09c-.22-.66-.35-1.36-.35-2.09s.13-1.43.35-2.09V7.07H2.18C1.43 8.55 1 10.22 1 12s.43 3.45 1.18 4.93l2.85-2.22.81-.62z" />
      <path d="M12 5.38c1.62 0 3.06.56 4.21 1.64l3.15-3.15C17.45 2.09 14.97 1 12 1 7.7 1 3.99 3.47 2.18 7.07l3.66 2.84c.87-2.6 3.3-4.53 6.16-4.53z" />
    </svg>
  );
}

function GitHubGlyph() {
  return (
    <svg className="h-4 w-4" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
      <path d="M12 .5C5.65.5.5 5.65.5 12c0 5.08 3.29 9.39 7.86 10.91.58.11.79-.25.79-.55 0-.27-.01-1.17-.02-2.12-3.2.7-3.87-1.36-3.87-1.36-.52-1.33-1.28-1.68-1.28-1.68-1.04-.71.08-.7.08-.7 1.15.08 1.76 1.19 1.76 1.19 1.03 1.75 2.69 1.25 3.35.95.1-.74.4-1.25.72-1.54-2.55-.29-5.24-1.28-5.24-5.69 0-1.26.45-2.28 1.19-3.09-.12-.29-.52-1.46.11-3.05 0 0 .97-.31 3.17 1.18a11.05 11.05 0 0 1 5.78 0c2.2-1.49 3.17-1.18 3.17-1.18.63 1.59.23 2.76.11 3.05.74.81 1.19 1.83 1.19 3.09 0 4.42-2.7 5.39-5.26 5.68.41.35.77 1.05.77 2.12 0 1.53-.01 2.76-.01 3.14 0 .3.2.66.8.55A11.52 11.52 0 0 0 23.5 12C23.5 5.65 18.35.5 12 .5z" />
    </svg>
  );
}
