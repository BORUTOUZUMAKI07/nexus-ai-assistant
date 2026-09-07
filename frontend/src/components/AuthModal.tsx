"use client";

import React, { useState } from "react";
import { X, Lock, Mail, User, ArrowRight, Sparkles, Loader2 } from "lucide-react";
import { registerUser, loginUser } from "@/lib/api";
import { setAccessToken } from "@/lib/auth";

interface AuthModalProps {
  isOpen: boolean;
  onClose: () => void;
  onSuccess: (user: { access_token: string }) => void;
}

export const AuthModal: React.FC<AuthModalProps> = ({ isOpen, onClose, onSuccess }) => {
  const [mode, setMode] = useState<"login" | "register">("login");
  const [email, setEmail] = useState("");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [fullName, setFullName] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  if (!isOpen) return null;

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
      const tokenRes = await loginUser({ email, password });
      setAccessToken(tokenRes.access_token);
      onSuccess(tokenRes);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Authentication failed. Please check your credentials.");
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 backdrop-blur-md p-4 animate-in fade-in duration-200">
      <div className="relative w-full max-w-md rounded-2xl bg-zinc-900/90 border border-white/10 p-6 shadow-2xl backdrop-blur-xl">
        {/* Close Button */}
        <button
          onClick={onClose}
          className="absolute right-4 top-4 rounded-lg p-1.5 text-zinc-400 hover:bg-white/5 hover:text-zinc-200 transition-colors"
        >
          <X className="h-5 w-5" />
        </button>

        {/* Header */}
        <div className="flex items-center gap-3 mb-6">
          <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-gradient-to-tr from-violet-600 to-cyan-500 text-white shadow-lg">
            <Sparkles className="h-5 w-5" />
          </div>
          <div>
            <h2 className="text-xl font-bold text-zinc-100">
              {mode === "login" ? "Welcome back" : "Create your account"}
            </h2>
            <p className="text-xs text-zinc-400">
              {mode === "login"
                ? "Sign in to access your Nexus assistant"
                : "Join Nexus for production AI workflows"}
            </p>
          </div>
        </div>

        {error && (
          <div className="mb-4 rounded-lg bg-rose-500/10 border border-rose-500/20 p-3 text-xs text-rose-300">
            {error}
          </div>
        )}

        {/* Form */}
        <form onSubmit={handleSubmit} className="space-y-4">
          {mode === "register" && (
            <>
              <div>
                <label className="block text-xs font-medium text-zinc-300 mb-1.5">Full Name</label>
                <div className="relative">
                  <User className="absolute left-3 top-2.5 h-4 w-4 text-zinc-500" />
                  <input
                    type="text"
                    required
                    value={fullName}
                    onChange={(e) => setFullName(e.target.value)}
                    placeholder="Jane Doe"
                    className="w-full rounded-xl bg-zinc-800/80 border border-white/5 pl-9 pr-4 py-2 text-sm text-zinc-100 placeholder-zinc-500 focus:border-violet-500/50 focus:outline-none focus:ring-1 focus:ring-violet-500/50"
                  />
                </div>
              </div>
              <div>
                <label className="block text-xs font-medium text-zinc-300 mb-1.5">Username</label>
                <div className="relative">
                  <User className="absolute left-3 top-2.5 h-4 w-4 text-zinc-500" />
                  <input
                    type="text"
                    required
                    value={username}
                    onChange={(e) => setUsername(e.target.value)}
                    placeholder="janedoe"
                    className="w-full rounded-xl bg-zinc-800/80 border border-white/5 pl-9 pr-4 py-2 text-sm text-zinc-100 placeholder-zinc-500 focus:border-violet-500/50 focus:outline-none focus:ring-1 focus:ring-violet-500/50"
                  />
                </div>
              </div>
            </>
          )}

          <div>
            <label className="block text-xs font-medium text-zinc-300 mb-1.5">Email address</label>
            <div className="relative">
              <Mail className="absolute left-3 top-2.5 h-4 w-4 text-zinc-500" />
              <input
                type="email"
                required
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                placeholder="name@example.com"
                className="w-full rounded-xl bg-zinc-800/80 border border-white/5 pl-9 pr-4 py-2 text-sm text-zinc-100 placeholder-zinc-500 focus:border-violet-500/50 focus:outline-none focus:ring-1 focus:ring-violet-500/50"
              />
            </div>
          </div>

          <div>
            <label className="block text-xs font-medium text-zinc-300 mb-1.5">Password</label>
            <div className="relative">
              <Lock className="absolute left-3 top-2.5 h-4 w-4 text-zinc-500" />
              <input
                type="password"
                required
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                placeholder="••••••••"
                className="w-full rounded-xl bg-zinc-800/80 border border-white/5 pl-9 pr-4 py-2 text-sm text-zinc-100 placeholder-zinc-500 focus:border-violet-500/50 focus:outline-none focus:ring-1 focus:ring-violet-500/50"
              />
            </div>
          </div>

          <button
            type="submit"
            disabled={loading}
            className="w-full mt-2 flex items-center justify-center gap-2 rounded-xl bg-gradient-to-r from-violet-600 to-indigo-600 hover:from-violet-500 hover:to-indigo-500 py-2.5 text-sm font-semibold text-white shadow-lg shadow-violet-500/20 transition-all disabled:opacity-50"
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
        <div className="mt-6 pt-4 border-t border-white/5 text-center text-xs text-zinc-400">
          {mode === "login" ? (
            <span>
              Don&apos;t have an account?{" "}
              <button
                onClick={() => setMode("register")}
                className="font-medium text-violet-400 hover:text-violet-300 hover:underline"
              >
                Sign up
              </button>
            </span>
          ) : (
            <span>
              Already have an account?{" "}
              <button
                onClick={() => setMode("login")}
                className="font-medium text-violet-400 hover:text-violet-300 hover:underline"
              >
                Sign in
              </button>
            </span>
          )}
        </div>
      </div>
    </div>
  );
};
