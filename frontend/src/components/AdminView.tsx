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
} from "lucide-react";
import {
  fetchHookPolicies,
  createHookPolicy,
  updateHookPolicy,
  deleteHookPolicy,
  HookPolicyItem,
  HookPolicyInput,
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

type Tab = "users" | "health" | "audit" | "hooks";

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

export const AdminView: React.FC = () => {
  const [activeTab, setActiveTab] = useState<Tab>("users");
  const [users, setUsers] = useState<AdminUser[]>([]);
  const [auditLogs, setAuditLogs] = useState<AuditLogItem[]>([]);
  const [systemHealth, setSystemHealth] = useState<SystemHealth | null>(null);
  const [hooks, setHooks] = useState<HookPolicyItem[]>([]);
  const [hookForm, setHookForm] = useState<HookPolicyInput>(EMPTY_HOOK_FORM);
  const [hookBusy, setHookBusy] = useState(false);
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

  const tabButton = (tab: Tab, label: string, Icon: React.ComponentType<{ className?: string }>) => (
    <button
      key={tab}
      onClick={() => setActiveTab(tab)}
      className={`flex items-center gap-2 rounded-lg px-3 py-1.5 text-xs font-medium transition-colors ${
        activeTab === tab
          ? "bg-[var(--accent-soft)] text-[var(--accent-hover)] border border-[var(--accent)]"
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
          <div className="flex h-9 w-9 items-center justify-center rounded-lg bg-[var(--bg-surface-elevated)] border border-[var(--border-subtle)] text-[var(--accent)]">
            <ShieldCheck className="h-4 w-4" />
          </div>
          <div>
            <h1 className="text-lg font-semibold tracking-tight text-white">
              Admin
            </h1>
            <p className="text-xs text-[var(--text-muted)]">
              Users, system health, and security audit trails
            </p>
          </div>
        </div>

        <button
          onClick={fetchAdminData}
          className="flex items-center gap-2 rounded-lg border border-[var(--border-subtle)] px-3 py-1.5 text-xs text-[var(--text-secondary)] hover:text-white hover:border-[var(--border-strong)] transition-colors"
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
      </div>

      {error && (
        <div className="flex items-center justify-between gap-4 rounded-xl border border-[var(--border-strong)] bg-[var(--bg-surface)] px-4 py-3 text-sm">
          <div className="flex items-center gap-3 min-w-0">
            <AlertCircle className="w-4 h-4 text-[var(--status-danger)] shrink-0" />
            <span className="text-[var(--text-secondary)] truncate">{error}</span>
          </div>
          <button
            onClick={fetchAdminData}
            className="flex items-center gap-1.5 rounded-lg border border-[var(--border-subtle)] px-2.5 py-1 text-xs text-[var(--text-secondary)] hover:text-white transition-colors whitespace-nowrap"
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
                    <td className="p-3.5 font-medium text-white">
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
                        className="rounded-lg border border-[var(--border-subtle)] bg-[var(--bg-main)] hover:border-[var(--border-strong)] px-2.5 py-1 text-[11px] text-[var(--text-secondary)] hover:text-white transition-colors"
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
                  <h3 className="text-sm font-medium text-white">{label}</h3>
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
            <h3 className="text-sm font-medium text-white mb-1">External Observability & Telemetry</h3>
            <p className="text-xs text-[var(--text-muted)] mb-4">
              Access real-time LLM trace monitoring, token latency analytics, and crash reporting dashboards.
            </p>
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
              <a
                href="https://us.helicone.ai"
                target="_blank"
                rel="noopener noreferrer"
                className="flex items-center justify-between p-3.5 rounded-lg border border-[var(--border-subtle)] bg-[var(--bg-main)] hover:border-[var(--accent)] text-xs text-[var(--text-secondary)] hover:text-white transition-colors group"
              >
                <div>
                  <div className="font-semibold text-white group-hover:text-[var(--accent)] transition-colors">
                    Helicone AI Observability
                  </div>
                  <div className="text-[11px] text-[var(--text-muted)]">Live LLM request tracing & cost metrics</div>
                </div>
                <ExternalLink className="w-4 h-4 text-[var(--text-muted)] group-hover:text-[var(--accent)] shrink-0" />
              </a>
              <a
                href="https://sentry.io"
                target="_blank"
                rel="noopener noreferrer"
                className="flex items-center justify-between p-3.5 rounded-lg border border-[var(--border-subtle)] bg-[var(--bg-main)] hover:border-[var(--accent)] text-xs text-[var(--text-secondary)] hover:text-white transition-colors group"
              >
                <div>
                  <div className="font-semibold text-white group-hover:text-[var(--accent)] transition-colors">
                    Sentry Error Tracking
                  </div>
                  <div className="text-[11px] text-[var(--text-muted)]">Backend exception monitoring & APM traces</div>
                </div>
                <ExternalLink className="w-4 h-4 text-[var(--text-muted)] group-hover:text-[var(--accent)] shrink-0" />
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
                    <td className="p-3.5 font-medium text-white">{log.action}</td>
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
            <h3 className="text-sm font-medium text-white mb-1 flex items-center gap-2">
              <Plus className="h-4 w-4 text-[var(--accent)]" />
              New hook policy
            </h3>
            <p className="text-xs text-[var(--text-muted)] mb-4">
              Pre/post tool policies run at the tool gateway:{" "}
              <span className="text-[var(--text-secondary)]">block</span> rejects the call,{" "}
              <span className="text-[var(--text-secondary)]">redact</span> strips a field,{" "}
              <span className="text-[var(--text-secondary)]">log</span> records the event only.
              Tool names support <code className="font-mono text-[var(--accent)]">*</code> wildcards.
            </p>
            <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-3">
              <input
                value={hookForm.name}
                onChange={(e) => setHookForm({ ...hookForm, name: e.target.value })}
                placeholder="Name (e.g. Block shell exec)"
                className="px-3 py-2 rounded-lg border border-[var(--border-subtle)] bg-[var(--bg-main)] text-xs text-white placeholder-[var(--text-muted)] focus:outline-none focus:border-[var(--accent)] transition-colors"
              />
              <input
                value={hookForm.tool_name}
                onChange={(e) => setHookForm({ ...hookForm, tool_name: e.target.value })}
                placeholder="Tool (e.g. run_shell, *)"
                className="px-3 py-2 rounded-lg border border-[var(--border-subtle)] bg-[var(--bg-main)] text-xs text-white placeholder-[var(--text-muted)] focus:outline-none focus:border-[var(--accent)] transition-colors"
              />
              <select
                value={hookForm.event}
                onChange={(e) =>
                  setHookForm({ ...hookForm, event: e.target.value as "pre_tool" | "post_tool" })
                }
                className="px-3 py-2 rounded-lg border border-[var(--border-subtle)] bg-[var(--bg-main)] text-xs text-white focus:outline-none focus:border-[var(--accent)] transition-colors"
              >
                <option value="pre_tool">pre_tool</option>
                <option value="post_tool">post_tool</option>
              </select>
              <select
                value={hookForm.action}
                onChange={(e) =>
                  setHookForm({ ...hookForm, action: e.target.value as "block" | "redact" | "log" })
                }
                className="px-3 py-2 rounded-lg border border-[var(--border-subtle)] bg-[var(--bg-main)] text-xs text-white focus:outline-none focus:border-[var(--accent)] transition-colors"
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
                className="px-3 py-2 rounded-lg border border-[var(--border-subtle)] bg-[var(--bg-main)] text-xs text-white placeholder-[var(--text-muted)] focus:outline-none focus:border-[var(--accent)] transition-colors"
              />
              <input
                value={hookForm.org_id ?? ""}
                onChange={(e) =>
                  setHookForm({ ...hookForm, org_id: e.target.value || null })
                }
                placeholder="Org id (empty = global)"
                className="px-3 py-2 rounded-lg border border-[var(--border-subtle)] bg-[var(--bg-main)] text-xs text-white placeholder-[var(--text-muted)] focus:outline-none focus:border-[var(--accent)] transition-colors"
              />
              <input
                value={hookForm.message ?? ""}
                onChange={(e) =>
                  setHookForm({ ...hookForm, message: e.target.value || null })
                }
                placeholder="Message (optional)"
                className="px-3 py-2 rounded-lg border border-[var(--border-subtle)] bg-[var(--bg-main)] text-xs text-white placeholder-[var(--text-muted)] focus:outline-none focus:border-[var(--accent)] transition-colors"
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
                      <td className="p-3.5 font-medium text-white">{h.name}</td>
                      <td className="p-3.5 font-mono text-[var(--accent)]">{h.tool_name}</td>
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
    </div>
  );
};