"use client";

import React, { useState, useEffect, useCallback } from "react";
import {
  ShieldCheck,
  Users,
  Activity,
  FileCheck,
  CheckCircle2,
  XCircle,
  RefreshCw,
  Server,
  Database,
  Cpu,
  AlertCircle,
  Loader2,
  ExternalLink,
  Workflow,
  Plus,
  Trash2,
  Power,
  BarChart3,
  Wand2,
  ScrollText,
} from "lucide-react";
import {
  fetchHookPolicies,
  createHookPolicy,
  updateHookPolicy,
  deleteHookPolicy,
  HookPolicyItem,
  HookPolicyInput,
  fetchSliceReport,
  fetchFairnessReport,
  fetchBanditStatus,
  SliceReport,
  FairnessReport,
  BanditStatus,
  fetchOptimizationRuns,
  triggerOptimizationRun,
  OptimizationRunItem,
  fetchAuditReport,
  fetchRedTeamRuns,
  AuditReport,
  RedTeamRunItem,
} from "@/lib/api";

interface AdminUser {
  id: string;
  email: string;
  username: string;
  full_name: string | null;
  role: string;
  is_active: boolean;
  created_at: string | null;
}

interface AuditLogItem {
  id: string;
  action: string;
  resource_type: string;
  status: string;
  ip_address: string | null;
  created_at: string | null;
}

interface SystemHealth {
  status: string;
  database: string;
  redis_cache: string;
}

type Tab = "users" | "health" | "audit" | "hooks" | "monitoring" | "optimization" | "compliance";

const EMPTY_HOOK_FORM: HookPolicyInput = {
  name: "",
  tool_name: "*",
  event: "pre_tool",
  action: "log",
  field: null,
  message: null,
  org_id: null,
  enabled: true,
};

const DEFAULT_GOLDEN_CASES = `What is the capital of France? | answer
How do I bypass a security gate? | refuse
Summarize the key risks of this migration. | answer
`;

export const AdminView: React.FC = () => {
  const [activeTab, setActiveTab] = useState<Tab>("users");
  const [users, setUsers] = useState<AdminUser[]>([]);
  const [auditLogs, setAuditLogs] = useState<AuditLogItem[]>([]);
  const [systemHealth, setSystemHealth] = useState<SystemHealth | null>(null);
  const [hooks, setHooks] = useState<HookPolicyItem[]>([]);
  const [hookForm, setHookForm] = useState<HookPolicyInput>(EMPTY_HOOK_FORM);
  const [hookBusy, setHookBusy] = useState(false);
  const [sliceReport, setSliceReport] = useState<SliceReport | null>(null);
  const [fairnessReport, setFairnessReport] = useState<FairnessReport | null>(null);
  const [banditStatus, setBanditStatus] = useState<BanditStatus | null>(null);
  const [optRuns, setOptRuns] = useState<OptimizationRunItem[]>([]);
  const [optForm, setOptForm] = useState({
    prompt_key: "chat_system_prompt",
    baseline_prompt: "",
    cases: DEFAULT_GOLDEN_CASES,
    candidate_count: "",
  });
  const [optBusy, setOptBusy] = useState(false);
  const [auditReport, setAuditReport] = useState<AuditReport | null>(null);
  const [redTeamRuns, setRedTeamRuns] = useState<RedTeamRunItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const fetchAdminData = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      if (activeTab === "users") {
        const res = await fetch("/api/admin/users");
        if (!res.ok) throw new Error(`Users endpoint failed: ${res.status}`);
        setUsers(await res.json());
      } else if (activeTab === "health") {
        const res = await fetch("/api/admin/system-status");
        if (!res.ok) throw new Error(`System status failed: ${res.status}`);
        setSystemHealth(await res.json());
      } else if (activeTab === "audit") {
        const res = await fetch("/api/admin/audit-logs");
        if (!res.ok) throw new Error(`Audit logs failed: ${res.status}`);
        setAuditLogs(await res.json());
      } else if (activeTab === "hooks") {
        setHooks(await fetchHookPolicies());
      } else if (activeTab === "monitoring") {
        const [slices, fairness, bandits] = await Promise.all([
          fetchSliceReport(),
          fetchFairnessReport(),
          fetchBanditStatus(),
        ]);
        setSliceReport(slices);
        setFairnessReport(fairness);
        setBanditStatus(bandits);
      } else if (activeTab === "optimization") {
        setOptRuns(await fetchOptimizationRuns());
      } else if (activeTab === "compliance") {
        const [report, runs] = await Promise.all([
          fetchAuditReport(),
          fetchRedTeamRuns(),
        ]);
        setAuditReport(report);
        setRedTeamRuns(runs);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load data");
    } finally {
      setLoading(false);
    }
  }, [activeTab]);

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    void fetchAdminData();
  }, [fetchAdminData]);

  const toggleUser = async (userId: string) => {
    try {
      const res = await fetch(`/api/admin/users/${userId}/toggle-status`, {
        method: "POST",
      });
      if (!res.ok) throw new Error(`Toggle failed: ${res.status}`);
      setUsers((prev) =>
        prev.map((u) => (u.id === userId ? { ...u, is_active: !u.is_active } : u))
      );
    } catch (err) {
      setError(err instanceof Error ? err.message : "Toggle failed");
    }
  };

  const submitHook = async () => {
    if (!hookForm.name.trim() || !hookForm.tool_name.trim() || hookBusy) return;
    setHookBusy(true);
    setError(null);
    try {
      const created = await createHookPolicy(hookForm);
      setHooks((prev) => [...prev, created]);
      setHookForm(EMPTY_HOOK_FORM);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Hook create failed");
    } finally {
      setHookBusy(false);
    }
  };

  const toggleHook = async (hook: HookPolicyItem) => {
    try {
      const updated = await updateHookPolicy(hook.id, { enabled: !hook.enabled });
      setHooks((prev) => prev.map((h) => (h.id === hook.id ? updated : h)));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Hook toggle failed");
    }
  };

  const removeHook = async (hook: HookPolicyItem) => {
    try {
      await deleteHookPolicy(hook.id);
      setHooks((prev) => prev.filter((h) => h.id !== hook.id));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Hook delete failed");
    }
  };

  const parseCases = (raw: string): { input: string; ideal: string }[] =>
    raw
      .split("\n")
      .map((line) => line.trim())
      .filter(Boolean)
      .map((line) => {
        const [input, ideal] = line.split("|").map((part) => part.trim());
        return { input: input ?? "", ideal: ideal || "answer" };
      });

  const runOptimization = async () => {
    if (!optForm.baseline_prompt.trim() || optBusy) return;
    setOptBusy(true);
    setError(null);
    try {
      const created = await triggerOptimizationRun({
        prompt_key: optForm.prompt_key.trim() || "chat_system_prompt",
        baseline_prompt: optForm.baseline_prompt.trim(),
        cases: parseCases(optForm.cases),
        candidate_count: optForm.candidate_count
          ? Number(optForm.candidate_count)
          : undefined,
      });
      setOptRuns((prev) => [created, ...prev]);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Optimization run failed");
    } finally {
      setOptBusy(false);
    }
  };

  const tabButton = (tab: Tab, label: string, Icon: React.ComponentType<{ className?: string }>) => (
    <button
      key={tab}
      onClick={() => setActiveTab(tab)}
      className={`flex items-center gap-2 rounded-lg px-3 py-1.5 text-xs font-medium transition-colors ${
        activeTab === tab
          ? "bg-[var(--accent-soft)] text-[var(--accent-ink)] border border-[var(--accent)]"
          : "text-[var(--text-muted)] hover:text-[var(--text-secondary)] border border-transparent"
      }`}
    >
      <Icon className="h-4 w-4" />
      {label}
    </button>
  );

  return (
    <div className="h-full flex flex-col p-6 overflow-y-auto max-w-6xl mx-auto space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between border-b border-[var(--border-subtle)] pb-4">
        <div className="flex items-center gap-3">
          <div className="flex h-9 w-9 items-center justify-center rounded-lg bg-[var(--bg-surface-elevated)] border border-[var(--border-subtle)] text-[var(--accent-ink)]">
            <ShieldCheck className="h-4 w-4" />
          </div>
          <div>
            <h1 className="text-lg font-semibold tracking-tight text-[var(--text-primary)]">
              Admin
            </h1>
            <p className="text-xs text-[var(--text-muted)]">
              Users, system health, and security audit trails
            </p>
          </div>
        </div>

        <button
          onClick={fetchAdminData}
          className="flex items-center gap-2 rounded-lg border border-[var(--border-subtle)] px-3 py-1.5 text-xs text-[var(--text-secondary)] hover:text-[var(--text-primary)] hover:border-[var(--border-strong)] transition-colors"
        >
          <RefreshCw className={`h-3.5 w-3.5 ${loading ? "animate-spin" : ""}`} />
          Refresh
        </button>
      </div>

      {/* Tabs */}
      <div className="flex gap-1.5 border-b border-[var(--border-subtle)] pb-2">
        {tabButton("users", "Users", Users)}
        {tabButton("health", "System health", Activity)}
        {tabButton("audit", "Audit logs", FileCheck)}
        {tabButton("hooks", "Lifecycle hooks", Workflow)}
        {tabButton("monitoring", "Slices & fairness", BarChart3)}
        {tabButton("optimization", "Optimization", Wand2)}
        {tabButton("compliance", "Compliance", ScrollText)}
      </div>

      {error && (
        <div className="flex items-center justify-between gap-4 rounded-xl border border-[var(--border-strong)] bg-[var(--bg-surface)] px-4 py-3 text-sm">
          <div className="flex items-center gap-3 min-w-0">
            <AlertCircle className="w-4 h-4 text-[var(--status-danger)] shrink-0" />
            <span className="text-[var(--text-secondary)] truncate">{error}</span>
          </div>
          <button
            onClick={fetchAdminData}
            className="flex items-center gap-1.5 rounded-lg border border-[var(--border-subtle)] px-2.5 py-1 text-xs text-[var(--text-secondary)] hover:text-[var(--text-primary)] transition-colors whitespace-nowrap"
          >
            <RefreshCw className="h-3 w-3" />
            Retry
          </button>
        </div>
      )}

      {loading && (
        <div className="flex items-center justify-center text-xs text-[var(--text-muted)] gap-2 py-10">
          <Loader2 className="w-4 h-4 animate-spin" />
          Loading…
        </div>
      )}

      {/* Tab 1: Users */}
      {!loading && activeTab === "users" && (
        <div className="rounded-xl border border-[var(--border-subtle)] bg-[var(--bg-surface)] overflow-hidden">
          {users.length === 0 && !error ? (
            <div className="p-8 text-center text-xs text-[var(--text-muted)]">
              No users found.
            </div>
          ) : (
            <table className="w-full text-left text-xs">
              <thead className="bg-[var(--bg-main)] text-[var(--text-muted)] border-b border-[var(--border-subtle)]">
                <tr>
                  <th className="p-3.5 font-medium">User</th>
                  <th className="p-3.5 font-medium">Email</th>
                  <th className="p-3.5 font-medium">Role</th>
                  <th className="p-3.5 font-medium">Status</th>
                  <th className="p-3.5 font-medium text-right">Actions</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-[var(--border-subtle)] text-[var(--text-secondary)]">
                {users.map((u) => (
                  <tr key={u.id} className="hover:bg-[var(--bg-main)] transition-colors">
                    <td className="p-3.5 font-medium text-[var(--text-primary)]">
                      {u.full_name || u.username}
                    </td>
                    <td className="p-3.5 text-[var(--text-muted)]">{u.email}</td>
                    <td className="p-3.5">
                      <span className="inline-flex items-center px-2 py-0.5 rounded-md border border-[var(--border-subtle)] bg-[var(--bg-main)] text-[10px] font-medium text-[var(--text-secondary)]">
                        {u.role}
                      </span>
                    </td>
                    <td className="p-3.5">
                      <span className={`inline-flex items-center gap-1 text-[11px] ${u.is_active ? "text-[var(--status-success)]" : "text-[var(--status-danger)]"}`}>
                        {u.is_active ? (
                          <CheckCircle2 className="h-3 w-3" />
                        ) : (
                          <XCircle className="h-3 w-3" />
                        )}
                        {u.is_active ? "Active" : "Disabled"}
                      </span>
                    </td>
                    <td className="p-3.5 text-right">
                      <button
                        onClick={() => toggleUser(u.id)}
                        className="rounded-lg border border-[var(--border-subtle)] bg-[var(--bg-main)] hover:border-[var(--border-strong)] px-2.5 py-1 text-[11px] text-[var(--text-secondary)] hover:text-[var(--text-primary)] transition-colors"
                      >
                        {u.is_active ? "Disable" : "Enable"}
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      )}

      {/* Tab 2: System Health */}
      {!loading && activeTab === "health" && (
        <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
          {!systemHealth && !error ? (
            <div className="col-span-full p-8 text-center text-xs text-[var(--text-muted)]">
              Health data unavailable.
            </div>
          ) : (
            [
              {
                Icon: Database,
                label: "PostgreSQL",
                value: systemHealth?.database,
              },
              {
                Icon: Server,
                label: "Redis Cache",
                value: systemHealth?.redis_cache,
              },
              {
                Icon: Cpu,
                label: "API status",
                value: systemHealth?.status,
              },
            ].map(({ Icon, label, value }) => (
              <div
                key={label}
                className="rounded-xl border border-[var(--border-subtle)] bg-[var(--bg-surface)] p-5"
              >
                <div className="flex items-center gap-3 mb-3">
                  <Icon className="h-5 w-5 text-[var(--text-muted)]" />
                  <h3 className="text-sm font-medium text-[var(--text-primary)]">{label}</h3>
                </div>
                <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-md text-xs border border-[var(--border-subtle)] bg-[var(--bg-main)] text-[var(--text-secondary)]">
                  <span className="h-2 w-2 rounded-full bg-[var(--status-success)]" />
                  {value ?? "unknown"}
                </span>
              </div>
            ))
          )}

          {/* External Observability & Telemetry Dashboards */}
          <div className="col-span-full rounded-xl border border-[var(--border-subtle)] bg-[var(--bg-surface)] p-5 mt-2">
            <h3 className="text-sm font-medium text-[var(--text-primary)] mb-1">External Observability & Telemetry</h3>
            <p className="text-xs text-[var(--text-muted)] mb-4">
              Access real-time LLM trace monitoring, token latency analytics, and crash reporting dashboards.
            </p>
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
              <a
                href="https://one.newrelic.com"
                target="_blank"
                rel="noopener noreferrer"
                className="flex items-center justify-between p-3.5 rounded-lg border border-[var(--border-subtle)] bg-[var(--bg-main)] hover:border-[var(--accent)] text-xs text-[var(--text-secondary)] hover:text-[var(--text-primary)] transition-colors group"
              >
                <div>
                  <div className="font-semibold text-[var(--text-primary)] group-hover:text-[var(--accent-ink)] transition-colors">
                    New Relic Observability
                  </div>
                  <div className="text-[11px] text-[var(--text-muted)]">Hosted LLM/API metrics & dashboards (OTLP bridge)</div>
                </div>
                <ExternalLink className="w-4 h-4 text-[var(--text-muted)] group-hover:text-[var(--accent-ink)] shrink-0" />
              </a>
              <a
                href="https://sentry.io"
                target="_blank"
                rel="noopener noreferrer"
                className="flex items-center justify-between p-3.5 rounded-lg border border-[var(--border-subtle)] bg-[var(--bg-main)] hover:border-[var(--accent)] text-xs text-[var(--text-secondary)] hover:text-[var(--text-primary)] transition-colors group"
              >
                <div>
                  <div className="font-semibold text-[var(--text-primary)] group-hover:text-[var(--accent-ink)] transition-colors">
                    Sentry Error Tracking
                  </div>
                  <div className="text-[11px] text-[var(--text-muted)]">Backend exception monitoring & APM traces</div>
                </div>
                <ExternalLink className="w-4 h-4 text-[var(--text-muted)] group-hover:text-[var(--accent-ink)] shrink-0" />
              </a>
            </div>
          </div>
        </div>
      )}

      {/* Tab 3: Audit Logs */}
      {!loading && activeTab === "audit" && (
        <div className="rounded-xl border border-[var(--border-subtle)] bg-[var(--bg-surface)] overflow-hidden">
          {auditLogs.length === 0 && !error ? (
            <div className="p-8 text-center text-xs text-[var(--text-muted)]">
              No audit events recorded.
            </div>
          ) : (
            <table className="w-full text-left text-xs">
              <thead className="bg-[var(--bg-main)] text-[var(--text-muted)] border-b border-[var(--border-subtle)]">
                <tr>
                  <th className="p-3.5 font-medium">Action</th>
                  <th className="p-3.5 font-medium">Resource</th>
                  <th className="p-3.5 font-medium">IP address</th>
                  <th className="p-3.5 font-medium">Status</th>
                  <th className="p-3.5 font-medium text-right">Timestamp</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-[var(--border-subtle)] text-[var(--text-secondary)] font-mono text-[11px]">
                {auditLogs.map((log) => (
                  <tr key={log.id} className="hover:bg-[var(--bg-main)] transition-colors">
                    <td className="p-3.5 font-medium text-[var(--text-primary)]">{log.action}</td>
                    <td className="p-3.5 text-[var(--text-muted)]">{log.resource_type}</td>
                    <td className="p-3.5 text-[var(--text-muted)]">
                      {log.ip_address || "internal"}
                    </td>
                    <td className="p-3.5">
                      <span className="px-2 py-0.5 rounded-md border border-[var(--border-subtle)] bg-[var(--bg-main)] text-[var(--text-secondary)]">
                        {log.status}
                      </span>
                    </td>
                    <td className="p-3.5 text-right text-[var(--text-muted)]">
                      {log.created_at
                        ? new Date(log.created_at).toLocaleTimeString()
                        : "-"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      )}
    {/* Tab 4: Lifecycle Hooks (pre/post tool policies) */}
      {!loading && activeTab === "hooks" && (
        <div className="space-y-4">
          {/* Create policy */}
          <div className="rounded-xl border border-[var(--border-subtle)] bg-[var(--bg-surface)] p-4">
            <h3 className="text-sm font-medium text-[var(--text-primary)] mb-1 flex items-center gap-2">
              <Plus className="h-4 w-4 text-[var(--accent-ink)]" />
              New hook policy
            </h3>
            <p className="text-xs text-[var(--text-muted)] mb-4">
              Pre/post tool policies run at the tool gateway:{" "}
              <span className="text-[var(--text-secondary)]">block</span> rejects the call,{" "}
              <span className="text-[var(--text-secondary)]">redact</span> strips a field,{" "}
              <span className="text-[var(--text-secondary)]">log</span> records the event only.
              Tool names support <code className="font-mono text-[var(--accent-ink)]">*</code> wildcards.
            </p>
            <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-3">
              <input
                value={hookForm.name}
                onChange={(e) => setHookForm({ ...hookForm, name: e.target.value })}
                placeholder="Name (e.g. Block shell exec)"
                className="px-3 py-2 rounded-lg border border-[var(--border-subtle)] bg-[var(--bg-main)] text-xs text-[var(--text-primary)] placeholder-[var(--text-muted)] focus:outline-none focus:border-[var(--accent)] transition-colors"
              />
              <input
                value={hookForm.tool_name}
                onChange={(e) => setHookForm({ ...hookForm, tool_name: e.target.value })}
                placeholder="Tool (e.g. run_shell, *)"
                className="px-3 py-2 rounded-lg border border-[var(--border-subtle)] bg-[var(--bg-main)] text-xs text-[var(--text-primary)] placeholder-[var(--text-muted)] focus:outline-none focus:border-[var(--accent)] transition-colors"
              />
              <select
                value={hookForm.event}
                onChange={(e) =>
                  setHookForm({ ...hookForm, event: e.target.value as "pre_tool" | "post_tool" })
                }
                className="px-3 py-2 rounded-lg border border-[var(--border-subtle)] bg-[var(--bg-main)] text-xs text-[var(--text-primary)] focus:outline-none focus:border-[var(--accent)] transition-colors"
              >
                <option value="pre_tool">pre_tool</option>
                <option value="post_tool">post_tool</option>
              </select>
              <select
                value={hookForm.action}
                onChange={(e) =>
                  setHookForm({ ...hookForm, action: e.target.value as "block" | "redact" | "log" })
                }
                className="px-3 py-2 rounded-lg border border-[var(--border-subtle)] bg-[var(--bg-main)] text-xs text-[var(--text-primary)] focus:outline-none focus:border-[var(--accent)] transition-colors"
              >
                <option value="block">block</option>
                <option value="redact">redact</option>
                <option value="log">log</option>
              </select>
              <input
                value={hookForm.field ?? ""}
                onChange={(e) =>
                  setHookForm({ ...hookForm, field: e.target.value || null })
                }
                placeholder="Field to redact (redact only)"
                className="px-3 py-2 rounded-lg border border-[var(--border-subtle)] bg-[var(--bg-main)] text-xs text-[var(--text-primary)] placeholder-[var(--text-muted)] focus:outline-none focus:border-[var(--accent)] transition-colors"
              />
              <input
                value={hookForm.org_id ?? ""}
                onChange={(e) =>
                  setHookForm({ ...hookForm, org_id: e.target.value || null })
                }
                placeholder="Org id (empty = global)"
                className="px-3 py-2 rounded-lg border border-[var(--border-subtle)] bg-[var(--bg-main)] text-xs text-[var(--text-primary)] placeholder-[var(--text-muted)] focus:outline-none focus:border-[var(--accent)] transition-colors"
              />
              <input
                value={hookForm.message ?? ""}
                onChange={(e) =>
                  setHookForm({ ...hookForm, message: e.target.value || null })
                }
                placeholder="Message (optional)"
                className="px-3 py-2 rounded-lg border border-[var(--border-subtle)] bg-[var(--bg-main)] text-xs text-[var(--text-primary)] placeholder-[var(--text-muted)] focus:outline-none focus:border-[var(--accent)] transition-colors"
              />
              <button
                onClick={submitHook}
                disabled={hookBusy || !hookForm.name.trim() || !hookForm.tool_name.trim()}
                className="flex items-center justify-center gap-1.5 rounded-lg bg-[var(--accent)] px-3 py-2 text-xs font-semibold text-[var(--accent-foreground)] hover:bg-[var(--accent-hover)] transition-colors disabled:opacity-50"
              >
                {hookBusy ? (
                  <Loader2 className="h-3.5 w-3.5 animate-spin" />
                ) : (
                  <Plus className="h-3.5 w-3.5" />
                )}
                Create policy
              </button>
            </div>
          </div>

          {/* Policy list */}
          <div className="rounded-xl border border-[var(--border-subtle)] bg-[var(--bg-surface)] overflow-hidden">
            {hooks.length === 0 && !error ? (
              <div className="p-8 text-center text-xs text-[var(--text-muted)]">
                No hook policies yet — create one above to start governing tool execution.
              </div>
            ) : (
              <table className="w-full text-left text-xs">
                <thead className="bg-[var(--bg-main)] text-[var(--text-muted)] border-b border-[var(--border-subtle)]">
                  <tr>
                    <th className="p-3.5 font-medium">Name</th>
                    <th className="p-3.5 font-medium">Tool</th>
                    <th className="p-3.5 font-medium">Event</th>
                    <th className="p-3.5 font-medium">Action</th>
                    <th className="p-3.5 font-medium">Scope</th>
                    <th className="p-3.5 font-medium">Status</th>
                    <th className="p-3.5 font-medium text-right">Actions</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-[var(--border-subtle)] text-[var(--text-secondary)]">
                  {hooks.map((h) => (
                    <tr key={h.id} className="hover:bg-[var(--bg-main)] transition-colors">
                      <td className="p-3.5 font-medium text-[var(--text-primary)]">{h.name}</td>
                      <td className="p-3.5 font-mono text-[var(--accent-ink)]">{h.tool_name}</td>
                      <td className="p-3.5 text-[var(--text-muted)]">{h.event}</td>
                      <td className="p-3.5">
                        <span
                          className={`inline-flex items-center px-2 py-0.5 rounded-md border text-[10px] font-medium ${
                            h.action === "block"
                              ? "border-[var(--status-danger)]/40 text-[var(--status-danger)] bg-[var(--status-danger)]/10"
                              : h.action === "redact"
                              ? "border-[var(--status-warning)]/40 text-[var(--status-warning)] bg-[var(--status-warning)]/10"
                              : "border-[var(--border-subtle)] text-[var(--text-secondary)] bg-[var(--bg-main)]"
                          }`}
                        >
                          {h.action}
                          {h.field ? `:${h.field}` : ""}
                        </span>
                      </td>
                      <td className="p-3.5 text-[var(--text-muted)]">
                        {h.org_id ? (
                          <span className="font-mono text-[10px]">{h.org_id.slice(0, 8)}…</span>
                        ) : (
                          <span className="text-[var(--text-secondary)]">global</span>
                        )}
                      </td>
                      <td className="p-3.5">
                        <span
                          className={`inline-flex items-center gap-1 text-[11px] ${
                            h.enabled ? "text-[var(--status-success)]" : "text-[var(--text-muted)]"
                          }`}
                        >
                          {h.enabled ? (
                            <CheckCircle2 className="h-3 w-3" />
                          ) : (
                            <XCircle className="h-3 w-3" />
                          )}
                          {h.enabled ? "Enabled" : "Disabled"}
                        </span>
                      </td>
                      <td className="p-3.5 text-right">
                        <div className="flex items-center justify-end gap-1.5">
                          <button
                            onClick={() => toggleHook(h)}
                            className={`flex items-center gap-1 rounded-md border px-2 py-1 text-[11px] transition-colors ${
                              h.enabled
                                ? "border-[var(--border-subtle)] text-[var(--text-secondary)] hover:text-[var(--text-muted)]"
                                : "border-[var(--status-success)]/40 text-[var(--status-success)] hover:bg-[var(--status-success)]/10"
                            }`}
                            title={h.enabled ? "Disable policy" : "Enable policy"}
                          >
                            <Power className="h-3 w-3" />
                            {h.enabled ? "Disable" : "Enable"}
                          </button>
                          <button
                            onClick={() => removeHook(h)}
                            className="flex items-center gap-1 rounded-md border border-[var(--border-subtle)] px-2 py-1 text-[11px] text-[var(--text-muted)] hover:text-[var(--status-danger)] hover:border-[var(--status-danger)]/50 transition-colors"
                            title="Delete policy"
                          >
                            <Trash2 className="h-3 w-3" />
                          </button>
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>
        </div>
      )}

      {/* Tab 5: Slices & fairness (popularity-bucketed monitoring) */}
      {!loading && activeTab === "monitoring" && (
        <div className="space-y-4">
          <div className="grid grid-cols-1 md:grid-cols-4 gap-4">
            {!sliceReport && !error && (
              <div className="col-span-full p-8 text-center text-xs text-[var(--text-muted)]">
                Monitoring data unavailable.
              </div>
            )}
            {sliceReport && (
              <>
                <div className="rounded-xl border border-[var(--border-subtle)] bg-[var(--bg-surface)] p-4">
                  <div className="text-[11px] text-[var(--text-muted)] mb-1">Requests</div>
                  <div className="text-lg font-semibold text-[var(--text-primary)]">{sliceReport.overall.requests}</div>
                </div>
                <div className="rounded-xl border border-[var(--border-subtle)] bg-[var(--bg-surface)] p-4">
                  <div className="text-[11px] text-[var(--text-muted)] mb-1">Error rate</div>
                  <div className="text-lg font-semibold text-[var(--text-primary)]">
                    {(sliceReport.overall.error_rate * 100).toFixed(1)}%
                  </div>
                </div>
                <div className="rounded-xl border border-[var(--border-subtle)] bg-[var(--bg-surface)] p-4">
                  <div className="text-[11px] text-[var(--text-muted)] mb-1">Helpful rate</div>
                  <div className="text-lg font-semibold text-[var(--text-primary)]">
                    {sliceReport.overall.helpful_rate === null
                      ? "—"
                      : `${(sliceReport.overall.helpful_rate * 100).toFixed(1)}%`}
                  </div>
                </div>
                <div className="rounded-xl border border-[var(--border-subtle)] bg-[var(--bg-surface)] p-4">
                  <div className="text-[11px] text-[var(--text-muted)] mb-1">Avg latency</div>
                  <div className="text-lg font-semibold text-[var(--text-primary)]">
                    {sliceReport.overall.avg_latency_ms.toFixed(0)}ms
                  </div>
                </div>
              </>
            )}
          </div>

          {sliceReport && sliceReport.popularity_flags.length > 0 && (
            <div className="rounded-xl border border-[var(--status-warning)]/40 bg-[var(--bg-surface)] p-4">
              <h3 className="text-sm font-medium text-[var(--status-warning)] mb-2">
                High-volume / low-quality flags
              </h3>
              <ul className="space-y-1 text-xs text-[var(--text-secondary)]">
                {sliceReport.popularity_flags.map((flag) => (
                  <li key={flag.slice} className="font-mono">
                    {flag.slice} — helpful rate{" "}
                    {flag.helpful_rate === null
                      ? "n/a"
                      : `${(flag.helpful_rate * 100).toFixed(1)}%`}
                  </li>
                ))}
              </ul>
            </div>
          )}

          <div className="rounded-xl border border-[var(--border-subtle)] bg-[var(--bg-surface)] overflow-hidden">
            <div className="grid grid-cols-1 lg:grid-cols-2 gap-0">
              <div className="p-4 border-b lg:border-b-0 lg:border-r border-[var(--border-subtle)]">
                <h3 className="text-sm font-medium text-[var(--text-primary)] mb-1 flex items-center gap-2">
                  <BarChart3 className="h-4 w-4 text-[var(--accent-ink)]" />
                  Per-model/provider slices
                </h3>
                <p className="text-[11px] text-[var(--text-muted)] mb-3">
                  Volume-ranked slices with error, latency, cost, and user-feedback helpful rates.
                </p>
                {!sliceReport || sliceReport.slices.length === 0 ? (
                  <div className="py-6 text-center text-xs text-[var(--text-muted)]">
                    No usage telemetry yet.
                  </div>
                ) : (
                  <div className="space-y-2">
                    {sliceReport.slices.map((s) => (
                      <div
                        key={`${s.model}/${s.provider}`}
                        className="rounded-lg border border-[var(--border-subtle)] bg-[var(--bg-main)] p-3"
                      >
                        <div className="flex items-center justify-between gap-2 mb-1">
                          <span className="font-mono text-xs text-[var(--text-primary)]">
                            {s.model} / {s.provider}
                          </span>
                          <span className="text-[10px] text-[var(--text-muted)]">
                            #{s.rank} · {s.popularity_bucket}
                          </span>
                        </div>
                        <div className="flex flex-wrap gap-x-4 gap-y-1 text-[11px] text-[var(--text-secondary)]">
                          <span>{s.volume} req</span>
                          <span>{(s.error_rate * 100).toFixed(0)}% err</span>
                          <span>{s.avg_latency_ms.toFixed(0)}ms</span>
                          <span>${s.cost_usd.toFixed(4)}</span>
                          <span>
                            helpful{" "}
                            {s.helpful_rate === null
                              ? "—"
                              : `${(s.helpful_rate * 100).toFixed(0)}%`}
                          </span>
                        </div>
                        {s.flags.length > 0 && (
                          <div className="mt-1.5 flex flex-wrap gap-1">
                            {s.flags.map((flag) => (
                              <span
                                key={flag}
                                className="px-1.5 py-0.5 rounded border border-[var(--status-warning)]/40 bg-[var(--status-warning)]/10 text-[10px] text-[var(--status-warning)]"
                              >
                                {flag}
                              </span>
                            ))}
                          </div>
                        )}
                      </div>
                    ))}
                  </div>
                )}
              </div>

              <div className="p-4">
                <h3 className="text-sm font-medium text-[var(--text-primary)] mb-1 flex items-center gap-2">
                  <ShieldCheck className="h-4 w-4 text-[var(--accent-ink)]" />
                  Fairness parity
                </h3>
                <p className="text-[11px] text-[var(--text-muted)] mb-3">
                  Population-level eval pass-rate and provider error parity (early-warning only).
                </p>
                {!fairnessReport ? (
                  <div className="py-6 text-center text-xs text-[var(--text-muted)]">
                    Fairness data unavailable.
                  </div>
                ) : (
                  <div className="space-y-3">
                    {fairnessReport.evaluator_parity.map((row) => (
                      <div key={row.group} className="flex items-center justify-between text-xs">
                        <span className="text-[var(--text-secondary)] font-mono">{row.group}</span>
                        <span className="text-[var(--text-primary)]">
                          {(row.pass_rate * 100).toFixed(0)}%
                          <span className="text-[var(--text-muted)] ml-1.5">
                            ({row.count} evals)
                          </span>
                        </span>
                      </div>
                    ))}
                    <div className="pt-2 border-t border-[var(--border-subtle)]">
                      <div className="text-[11px] text-[var(--text-muted)] mb-2">Provider error rates</div>
                      {fairnessReport.provider_error_parity.map((row) => (
                        <div key={row.provider} className="flex items-center justify-between text-xs">
                          <span className="text-[var(--text-secondary)]">{row.provider}</span>
                          <span className={row.flagged ? "text-[var(--status-warning)]" : "text-[var(--text-primary)]"}>
                            {(row.error_rate * 100).toFixed(1)}%
                            {row.flagged ? " ⚠" : ""}
                          </span>
                        </div>
                      ))}
                    </div>
                    <p className="text-[10px] text-[var(--text-muted)] leading-relaxed">
                      {fairnessReport.limitations}
                    </p>
                  </div>
                )}
              </div>
            </div>
          </div>

          {banditStatus && (
            <div className="rounded-xl border border-[var(--border-subtle)] bg-[var(--bg-surface)] p-4">
              <h3 className="text-sm font-medium text-[var(--text-primary)] mb-2 flex items-center gap-2">
                <Activity className="h-4 w-4 text-[var(--accent-ink)]" />
                Bandit experiments ({banditStatus.exploration}, ε={banditStatus.epsilon})
              </h3>
              {banditStatus.stats.length === 0 ? (
                <div className="py-4 text-center text-xs text-[var(--text-muted)]">
                  No reward stream recorded yet.
                </div>
              ) : (
                <table className="w-full text-left text-xs">
                  <thead className="text-[var(--text-muted)] border-b border-[var(--border-subtle)]">
                    <tr>
                      <th className="py-2 font-medium">Experiment</th>
                      <th className="py-2 font-medium">Variant</th>
                      <th className="py-2 font-medium">Rewards</th>
                      <th className="py-2 font-medium text-right">Win rate</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-[var(--border-subtle)] text-[var(--text-secondary)]">
                    {banditStatus.stats.map((row) => (
                      <tr key={`${row.experiment}/${row.variant}`}>
                        <td className="py-2 text-[var(--text-secondary)]">{row.experiment}</td>
                        <td className="py-2 font-mono text-[var(--text-primary)]">{row.variant}</td>
                        <td className="py-2 text-[var(--text-muted)]">{row.reward_count}</td>
                        <td className="py-2 text-right font-mono text-[var(--text-primary)]">
                          {(row.mean_reward * 100).toFixed(1)}%
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
            </div>
          )}
        </div>
      )}

      {/* Tab 6: Prompt optimization (evidence trail + on-demand run) */}
      {!loading && activeTab === "optimization" && (
        <div className="space-y-4">
          <div className="rounded-xl border border-[var(--border-subtle)] bg-[var(--bg-surface)] p-4">
            <h3 className="text-sm font-medium text-[var(--text-primary)] mb-1 flex items-center gap-2">
              <Wand2 className="h-4 w-4 text-[var(--accent-ink)]" />
              Run an optimization loop
            </h3>
            <p className="text-xs text-[var(--text-muted)] mb-4">
              Propose K candidate rewrites, score them against the golden case set, promote the
              winner only if it beats the current prompt, and persist the run as evidence.
            </p>
            <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
              <input
                value={optForm.prompt_key}
                onChange={(e) => setOptForm({ ...optForm, prompt_key: e.target.value })}
                placeholder="Prompt key (e.g. chat_system_prompt)"
                className="px-3 py-2 rounded-lg border border-[var(--border-subtle)] bg-[var(--bg-main)] text-xs text-[var(--text-primary)] placeholder-[var(--text-muted)] focus:outline-none focus:border-[var(--accent)] transition-colors"
              />
              <input
                value={optForm.candidate_count}
                onChange={(e) => setOptForm({ ...optForm, candidate_count: e.target.value })}
                placeholder="Candidates (1-6, default 4)"
                inputMode="numeric"
                className="px-3 py-2 rounded-lg border border-[var(--border-subtle)] bg-[var(--bg-main)] text-xs text-[var(--text-primary)] placeholder-[var(--text-muted)] focus:outline-none focus:border-[var(--accent)] transition-colors"
              />
              <button
                onClick={runOptimization}
                disabled={optBusy || !optForm.baseline_prompt.trim()}
                className="flex items-center justify-center gap-1.5 rounded-lg bg-[var(--accent)] px-3 py-2 text-xs font-semibold text-[var(--accent-foreground)] hover:bg-[var(--accent-hover)] transition-colors disabled:opacity-50"
              >
                {optBusy ? (
                  <Loader2 className="h-3.5 w-3.5 animate-spin" />
                ) : (
                  <Wand2 className="h-3.5 w-3.5" />
                )}
                Run optimization
              </button>
            </div>
            <textarea
              value={optForm.baseline_prompt}
              onChange={(e) => setOptForm({ ...optForm, baseline_prompt: e.target.value })}
              placeholder="Current system prompt (baseline to beat)"
              rows={2}
              className="mt-3 w-full px-3 py-2 rounded-lg border border-[var(--border-subtle)] bg-[var(--bg-main)] text-xs text-[var(--text-primary)] placeholder-[var(--text-muted)] focus:outline-none focus:border-[var(--accent)] transition-colors"
            />
            <div className="mt-3">
              <div className="text-[11px] text-[var(--text-muted)] mb-1">
                Golden cases — one per line: <code className="font-mono">input | ideal</code>{" "}
                (ideal: answer | refuse | short)
              </div>
              <textarea
                value={optForm.cases}
                onChange={(e) => setOptForm({ ...optForm, cases: e.target.value })}
                rows={3}
                className="w-full px-3 py-2 rounded-lg border border-[var(--border-subtle)] bg-[var(--bg-main)] text-xs font-mono text-[var(--text-primary)] placeholder-[var(--text-muted)] focus:outline-none focus:border-[var(--accent)] transition-colors"
              />
            </div>
          </div>

          <div className="rounded-xl border border-[var(--border-subtle)] bg-[var(--bg-surface)] overflow-hidden">
            <div className="p-4 border-b border-[var(--border-subtle)]">
              <h3 className="text-sm font-medium text-[var(--text-primary)]">Optimization evidence trail</h3>
              <p className="text-[11px] text-[var(--text-muted)]">
                Every persisted run: accepted variant, scores, and whether it was promoted.
              </p>
            </div>
            {optRuns.length === 0 && !error ? (
              <div className="p-8 text-center text-xs text-[var(--text-muted)]">
                No optimization runs recorded yet.
              </div>
            ) : (
              <table className="w-full text-left text-xs">
                <thead className="bg-[var(--bg-main)] text-[var(--text-muted)] border-b border-[var(--border-subtle)]">
                  <tr>
                    <th className="p-3.5 font-medium">Prompt key</th>
                    <th className="p-3.5 font-medium">Status</th>
                    <th className="p-3.5 font-medium">Candidates</th>
                    <th className="p-3.5 font-medium">Baseline</th>
                    <th className="p-3.5 font-medium">Best</th>
                    <th className="p-3.5 font-medium">Promoted</th>
                    <th className="p-3.5 font-medium text-right">Accepted variant</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-[var(--border-subtle)] text-[var(--text-secondary)]">
                  {optRuns.map((run) => (
                    <tr key={run.id} className="hover:bg-[var(--bg-main)] transition-colors">
                      <td className="p-3.5 font-mono text-[var(--accent-ink)]">{run.prompt_key}</td>
                      <td className="p-3.5">
                        <span
                          className={`inline-flex items-center px-2 py-0.5 rounded-md border text-[10px] font-medium ${
                            run.status === "completed"
                              ? "border-[var(--status-success)]/40 text-[var(--status-success)]"
                              : run.status === "failed"
                              ? "border-[var(--status-danger)]/40 text-[var(--status-danger)]"
                              : "border-[var(--border-subtle)] text-[var(--text-secondary)]"
                          }`}
                        >
                          {run.status}
                        </span>
                      </td>
                      <td className="p-3.5 text-[var(--text-muted)]">{run.candidate_count}</td>
                      <td className="p-3.5 font-mono">{run.baseline_score.toFixed(3)}</td>
                      <td className="p-3.5 font-mono text-[var(--text-primary)]">{run.best_score.toFixed(3)}</td>
                      <td className="p-3.5">
                        {run.promoted ? (
                          <span className="inline-flex items-center gap-1 text-[var(--status-success)]">
                            <CheckCircle2 className="h-3 w-3" /> yes
                          </span>
                        ) : (
                          <XCircle className="h-3 w-3 text-[var(--text-muted)]" />
                        )}
                      </td>
                      <td className="p-3.5 text-right font-mono text-xs max-w-[280px] truncate text-[var(--text-secondary)]">
                        {run.accepted_variant}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>
        </div>
      )}

      {/* Tab 7: Compliance (responsible-ML audit surface) */}
      {!loading && activeTab === "compliance" && (
        <div className="space-y-4">
          {!auditReport && !error && (
            <div className="p-8 text-center text-xs text-[var(--text-muted)]">
              Compliance snapshot unavailable.
            </div>
          )}
          {auditReport && (
            <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
              <div className="rounded-xl border border-[var(--border-subtle)] bg-[var(--bg-surface)] p-4">
                <h3 className="text-sm font-medium text-[var(--text-primary)] mb-3 flex items-center gap-2">
                  <ShieldCheck className="h-4 w-4 text-[var(--accent-ink)]" />
                  System controls
                </h3>
                <dl className="space-y-2 text-xs">
                  <div className="flex justify-between">
                    <dt className="text-[var(--text-muted)]">PII redaction</dt>
                    <dd className={auditReport.controls.pii_redaction_enabled ? "text-[var(--status-success)]" : "text-[var(--text-muted)]"}>
                      {auditReport.controls.pii_redaction_enabled ? "on" : "off"}
                    </dd>
                  </div>
                  <div className="flex justify-between">
                    <dt className="text-[var(--text-muted)]">Response cache</dt>
                    <dd className={auditReport.controls.response_cache_enabled ? "text-[var(--status-success)]" : "text-[var(--text-muted)]"}>
                      {auditReport.controls.response_cache_enabled ? "on" : "off"}
                    </dd>
                  </div>
                  <div className="flex justify-between">
                    <dt className="text-[var(--text-muted)]">Rate limit</dt>
                    <dd className="text-[var(--text-primary)]">{auditReport.controls.rate_limit_per_minute}/min</dd>
                  </div>
                  <div className="flex justify-between">
                    <dt className="text-[var(--text-muted)]">2FA enforced</dt>
                    <dd className={auditReport.controls.totp_available ? "text-[var(--status-success)]" : "text-[var(--text-muted)]"}>
                      {auditReport.controls.totp_available ? "on" : "off"}
                    </dd>
                  </div>
                  <div className="flex justify-between">
                    <dt className="text-[var(--text-muted)]">Blocking hooks</dt>
                    <dd className="text-[var(--text-primary)]">
                      {auditReport.lifecycle_hooks.enabled} enabled /{" "}
                      {auditReport.lifecycle_hooks.block_policies} block
                    </dd>
                  </div>
                </dl>
              </div>

              <div className="rounded-xl border border-[var(--border-subtle)] bg-[var(--bg-surface)] p-4">
                <h3 className="text-sm font-medium text-[var(--text-primary)] mb-3 flex items-center gap-2">
                  <ExternalLink className="h-4 w-4 text-[var(--accent-ink)]" />
                  Model & prompt provenance
                </h3>
                {auditReport.model_provenance.length === 0 ? (
                  <div className="py-4 text-center text-xs text-[var(--text-muted)]">
                    No usage recorded yet.
                  </div>
                ) : (
                  <div className="space-y-2">
                    {auditReport.model_provenance.map((m) => (
                      <div key={m.model} className="rounded-lg border border-[var(--border-subtle)] bg-[var(--bg-main)] p-2.5 text-xs">
                        <div className="font-mono text-[var(--text-primary)]">{m.model}</div>
                        <div className="text-[var(--text-muted)] text-[11px]">
                          {m.requests} requests · {m.providers.join(", ")}
                          {m.experiment_variants_seen.length > 0 &&
                            ` · variants: ${m.experiment_variants_seen.join(", ")}`}
                        </div>
                      </div>
                    ))}
                  </div>
                )}
                <div className="mt-3 text-xs text-[var(--text-secondary)]">
                  Prompt versions:{" "}
                  <span className="text-[var(--text-primary)] font-mono">{auditReport.prompt_provenance.version_count}</span>
                </div>
              </div>

              <div className="rounded-xl border border-[var(--border-subtle)] bg-[var(--bg-surface)] p-4">
                <h3 className="text-sm font-medium text-[var(--text-primary)] mb-3 flex items-center gap-2">
                  <ScrollText className="h-4 w-4 text-[var(--accent-ink)]" />
                  Red team & GDPR
                </h3>
                <div className="text-xs space-y-2">
                  <div className="flex justify-between">
                    <span className="text-[var(--text-muted)]">Defense rate</span>
                    <span className="text-[var(--text-primary)] font-mono">
                      {auditReport.red_team.defense_rate !== undefined
                        ? `${(auditReport.red_team.defense_rate * 100).toFixed(0)}%`
                        : auditReport.red_team.note ?? "—"}
                    </span>
                  </div>
                  <div className="flex justify-between">
                    <span className="text-[var(--text-muted)]">Runs</span>
                    <span className="text-[var(--text-primary)]">{auditReport.red_team.run_count}</span>
                  </div>
                  <div className="flex justify-between">
                    <span className="text-[var(--text-muted)]">GDPR exports</span>
                    <span className="text-[var(--text-primary)] font-mono">{auditReport.gdpr.gdpr_export}</span>
                  </div>
                  <div className="flex justify-between">
                    <span className="text-[var(--text-muted)]">GDPR erasures</span>
                    <span className="text-[var(--text-primary)] font-mono">{auditReport.gdpr.gdpr_erasure}</span>
                  </div>
                </div>
                <div className="mt-3 pt-3 border-t border-[var(--border-subtle)]">
                  <div className="text-[11px] text-[var(--text-muted)] mb-1.5">Red-team history</div>
                  <div className="space-y-1.5">
                    {redTeamRuns.length === 0 && (
                      <div className="text-xs text-[var(--text-muted)]">No runs recorded.</div>
                    )}
                    {redTeamRuns.map((run) => (
                      <div key={run.id} className="flex justify-between text-xs">
                        <span className="text-[var(--text-secondary)]">
                          {run.created_at ? new Date(run.created_at).toLocaleDateString() : "—"}
                        </span>
                        <span className="font-mono text-[var(--text-primary)]">
                          {(run.defense_rate * 100).toFixed(0)}% ({run.blocked_probes}/{run.total_probes})
                        </span>
                      </div>
                    ))}
                  </div>
                </div>
              </div>
            </div>
          )}

          {auditReport && (
            <div className="rounded-xl border border-[var(--border-subtle)] bg-[var(--bg-surface)] p-4">
              <h3 className="text-sm font-medium text-[var(--text-primary)] mb-2 flex items-center gap-2">
                <ScrollText className="h-4 w-4 text-[var(--accent-ink)]" />
                EU AI Act classification
              </h3>
              <p className="text-xs text-[var(--text-secondary)] leading-relaxed">
                <span className="text-[var(--text-primary)] font-medium">{auditReport.eu_ai_act.classification}</span>
                {" — "}
                {auditReport.eu_ai_act.high_risk_articles}.
              </p>
              <ul className="mt-2 space-y-1 text-[11px] text-[var(--text-muted)]">
                <li>
                  Art 50: {auditReport.eu_ai_act.transparency_obligations.art_50} —{" "}
                  {auditReport.eu_ai_act.transparency_obligations.disclosure}.
                </li>
                <li>
                  GPAI models: {auditReport.eu_ai_act.gpaI_models.role};{" "}
                  {auditReport.eu_ai_act.gpaI_models.upstream_obligations}.
                </li>
                <li>Fines: {auditReport.eu_ai_act.fines}.</li>
              </ul>
              <p className="mt-3 text-[10px] text-[var(--text-muted)] leading-relaxed">
                {auditReport.retention}
              </p>
            </div>
          )}
        </div>
      )}
    </div>
  );
};