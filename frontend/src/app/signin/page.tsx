"use client";

import React, { useEffect, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { Loader2, Lock, Mail, User, ArrowRight, Sparkles } from "lucide-react";
import { registerUser, loginUser } from "@/lib/api";
import { checkAuth } from "@/lib/auth";

export default function SignInPage() {
  const router = useRouter();
  const [mode, setMode] = useState<"login" | "register">("login");
  const [email, setEmail] = useState("");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [fullName, setFullName] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    // The access cookie is httpOnly, so session presence is verified against
    // the backend rather than by reading document.cookie.
    void checkAuth().then((authed) => {
      if (authed) router.replace("/app");
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

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
      // /api/auth/login writes the httpOnly session cookies; this page just
      // navigates once the server confirms the session was created.
      await loginUser({ email, password });
      router.replace("/app");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Authentication failed. Please check your credentials.");
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="flex min-h-screen items-center justify-center bg-[var(--bg-main)] px-4">
      <div className="w-full max-w-md">
        <div className="mb-8 flex flex-col items-center text-center">
          <Link href="/" className="flex items-center gap-3">
            <span className="grid h-10 w-10 place-items-center rounded-lg shadow-float" aria-hidden="true">
              <Sparkles className="text-white" size={22} strokeWidth={2.2} />
            </span>
          </Link>
          <h1 className="mt-5 text-2xl font-semibold tracking-tight text-[var(--text-primary)]">
            {mode === "login" ? "Welcome back" : "Create your account"}
          </h1>
          <p className="mt-1.5 text-sm text-[var(--text-muted)]">
            {mode === "login"
              ? "Sign in to access your Nexus assistant"
              : "Join Nexus for production AI workflows"}
          </p>
        </div>

        {error && (
          <div className="mb-4 rounded-lg border border-[var(--border-strong)] bg-[var(--bg-main)] p-3 text-xs text-[var(--status-danger)]">
            {error}
          </div>
        )}

        <form
          onSubmit={handleSubmit}
          className="space-y-4 rounded-xl border border-[var(--border-subtle)] bg-[var(--bg-surface)] p-6 shadow-float"
        >
          {mode === "register" && (
            <>
              <div>
                <label className="mb-1.5 block text-xs font-medium text-[var(--text-secondary)]">Full Name</label>
                <div className="relative">
                  <User className="absolute left-3 top-2.5 h-4 w-4 text-[var(--text-faint)]" />
                  <input
                    type="text"
                    value={fullName}
                    onChange={(e) => setFullName(e.target.value)}
                    placeholder="Jane Doe"
                    className="w-full rounded-lg border border-[var(--border-subtle)] bg-[var(--bg-main)] py-2 pl-9 pr-4 text-sm text-[var(--text-primary)] placeholder-[var(--text-faint)] focus:border-[var(--accent)] focus:outline-none focus:ring-1 focus:ring-[var(--accent)]"
                  />
                </div>
              </div>
              <div>
                <label className="mb-1.5 block text-xs font-medium text-[var(--text-secondary)]">Username</label>
                <div className="relative">
                  <User className="absolute left-3 top-2.5 h-4 w-4 text-[var(--text-faint)]" />
                  <input
                    type="text"
                    value={username}
                    onChange={(e) => setUsername(e.target.value)}
                    placeholder="janedoe"
                    className="w-full rounded-lg border border-[var(--border-subtle)] bg-[var(--bg-main)] py-2 pl-9 pr-4 text-sm text-[var(--text-primary)] placeholder-[var(--text-faint)] focus:border-[var(--accent)] focus:outline-none focus:ring-1 focus:ring-[var(--accent)]"
                  />
                </div>
              </div>
            </>
          )}

          <div>
            <label className="mb-1.5 block text-xs font-medium text-[var(--text-secondary)]">Email address</label>
            <div className="relative">
              <Mail className="absolute left-3 top-2.5 h-4 w-4 text-[var(--text-faint)]" />
              <input
                type="email"
                required
                autoComplete="email"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                placeholder="name@example.com"
                className="w-full rounded-lg border border-[var(--border-subtle)] bg-[var(--bg-main)] py-2 pl-9 pr-4 text-sm text-[var(--text-primary)] placeholder-[var(--text-faint)] focus:border-[var(--accent)] focus:outline-none focus:ring-1 focus:ring-[var(--accent)]"
              />
            </div>
          </div>

          <div>
            <label className="mb-1.5 block text-xs font-medium text-[var(--text-secondary)]">Password</label>
            <div className="relative">
              <Lock className="absolute left-3 top-2.5 h-4 w-4 text-[var(--text-faint)]" />
              <input
                type="password"
                required
                autoComplete={mode === "login" ? "current-password" : "new-password"}
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                placeholder="••••••••"
                className="w-full rounded-lg border border-[var(--border-subtle)] bg-[var(--bg-main)] py-2 pl-9 pr-4 text-sm text-[var(--text-primary)] placeholder-[var(--text-faint)] focus:border-[var(--accent)] focus:outline-none focus:ring-1 focus:ring-[var(--accent)]"
              />
            </div>
          </div>

          <button
            type="submit"
            disabled={loading}
            className="mt-2 flex w-full items-center justify-center gap-2 rounded-lg bg-[var(--accent)] px-4 py-2.5 text-sm font-semibold text-[var(--accent-foreground)] shadow-sm transition-all hover:bg-[var(--accent-hover)] disabled:opacity-50"
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

        <div className="mt-5 text-center text-sm text-[var(--text-muted)]">
          {mode === "login" ? (
            <span>
              Don&apos;t have an account?{" "}
              <button
                type="button"
                onClick={() => setMode("register")}
                className="font-medium text-[var(--accent-hover)] hover:text-[var(--accent)] hover:underline"
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
                className="font-medium text-[var(--accent-hover)] hover:text-[var(--accent)] hover:underline"
              >
                Sign in
              </button>
            </span>
          )}
        </div>

        <div className="mt-6 text-center">
          <Link
            href="/"
            className="inline-flex items-center gap-1.5 text-xs text-[var(--text-faint)] transition-colors hover:text-[var(--text-secondary)]"
          >
            <ArrowRight className="h-3.5 w-3.5 rotate-180" />
            Back to home
          </Link>
        </div>
      </div>
    </div>
  );
}