"use client";

import React, { useState } from "react";
import { X, Lock, Mail, User, ArrowRight, Sparkles, Loader2 } from "lucide-react";
import { registerUser, loginUser } from "@/lib/api";
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
