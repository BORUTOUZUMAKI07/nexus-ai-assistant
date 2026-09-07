"use client";

import React, { useState, useEffect, useCallback } from "react";
import {
  BarChart3,
  Zap,
  DollarSign,
  Activity,
  Hash,
  ArrowDownToLine,
  Clock3,
  RefreshCw,
  AlertCircle,
} from "lucide-react";
import { fetchUsage, UsageStats } from "@/lib/api";

interface StatCardProps {
  icon: React.ReactNode;
  label: string;
  value: string;
  hint?: string;
}

const StatCard: React.FC<StatCardProps> = ({ icon, label, value, hint }) => (
  <div className="glass-panel glass-panel-hover p-4 space-y-1.5">
    <div className="flex items-center gap-2 text-[11px] text-[var(--text-muted)]">
      {icon}
      <span>{label}</span>
    </div>
    <div className="text-xl font-semibold text-white font-mono tracking-tight">
      {value}
    </div>
    {hint && <div className="text-[10px] text-[var(--text-faint)]">{hint}</div>}
  </div>
);

export const UsageView: React.FC = () => {
  const [stats, setStats] = useState<UsageStats | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setIsLoading(true);
    setError(null);
    try {
      const data = await fetchUsage();
      setStats(data);
    } catch (err) {
      setStats(null);
      setError(
        err instanceof Error ? err.message : "Could not load usage data"
      );
    } finally {
      setIsLoading(false);
    }
  }, []);

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    void load();
  }, [load]);

  return (
    <div className="flex-1 overflow-y-auto p-8 max-w-4xl mx-auto space-y-8">
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-xl font-semibold tracking-tight text-white flex items-center gap-2">
            <BarChart3 className="w-5 h-5 text-[var(--accent)]" /> Usage
          </h2>
          <p className="text-xs text-[var(--text-muted)] mt-1">
            Token consumption and cost across your sessions.
          </p>
        </div>
        <button
          onClick={load}
          disabled={isLoading}
          className="flex items-center gap-1.5 rounded-lg border border-[var(--border-subtle)] px-3 py-1.5 text-xs text-[var(--text-secondary)] hover:text-white hover:border-[var(--border-strong)] transition-colors disabled:opacity-50"
        >
          <RefreshCw className={`w-3.5 h-3.5 ${isLoading ? "animate-spin" : ""}`} />
          Refresh
        </button>
      </div>

      {isLoading && (
        <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
          {Array.from({ length: 4 }).map((_, i) => (
            <div key={i} className="glass-panel h-24 animate-pulse" />
          ))}
        </div>
      )}

      {error && (
        <div className="flex items-center gap-3 rounded-xl border border-[var(--border-strong)] bg-[var(--bg-surface)] p-4 text-sm">
          <AlertCircle className="w-4 h-4 text-[var(--status-danger)] shrink-0" />
          <span className="text-[var(--text-secondary)]">{error}</span>
        </div>
      )}

      {!isLoading && !error && stats && (
        <>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
            <StatCard
              icon={<Zap className="w-3.5 h-3.5 text-[var(--accent)]" />}
              label="Total tokens"
              value={stats.total_tokens.toLocaleString("en-US")}
            />
            <StatCard
              icon={<DollarSign className="w-3.5 h-3.5 text-[var(--text-secondary)]" />}
              label="Estimated cost"
              value={`$${stats.total_cost_usd.toFixed(4)}`}
            />
            <StatCard
              icon={<Clock3 className="w-3.5 h-3.5 text-[var(--text-secondary)]" />}
              label="Avg latency"
              value={`${Math.round(stats.average_latency_ms)}ms`}
            />
            <StatCard
              icon={<Hash className="w-3.5 h-3.5 text-[var(--text-secondary)]" />}
              label="Requests"
              value={stats.total_requests.toLocaleString("en-US")}
            />
          </div>

          <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
            <StatCard
              icon={<ArrowDownToLine className="w-3.5 h-3.5 text-[var(--text-secondary)]" />}
              label="Input tokens"
              value={stats.prompt_tokens.toLocaleString("en-US")}
              hint={`${stats.cached_tokens.toLocaleString("en-US")} cached`}
            />
            <StatCard
              icon={<Activity className="w-3.5 h-3.5 text-[var(--text-secondary)]" />}
              label="Output tokens"
              value={stats.completion_tokens.toLocaleString("en-US")}
            />
            <StatCard
              icon={<Hash className="w-3.5 h-3.5 text-[var(--text-secondary)]" />}
              label="Cached tokens"
              value={stats.cached_tokens.toLocaleString("en-US")}
            />
          </div>
        </>
      )}
    </div>
  );
};