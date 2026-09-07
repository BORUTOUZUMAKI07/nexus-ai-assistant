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
} from "lucide-react";

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

type Tab = "users" | "health" | "audit";

export const AdminView: React.FC = () => {
  const [activeTab, setActiveTab] = useState<Tab>("users");
  const [users, setUsers] = useState<AdminUser[]>([]);
  const [auditLogs, setAuditLogs] = useState<AuditLogItem[]>([]);
  const [systemHealth, setSystemHealth] = useState<SystemHealth | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const fetchAdminData = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      if (activeTab === "users") {
        const res = await fetch("/api/v1/admin/users");
        if (!res.ok) throw new Error(`Users endpoint failed: ${res.status}`);
        setUsers(await res.json());
      } else if (activeTab === "health") {
        const res = await fetch("/api/v1/admin/system-status");
        if (!res.ok) throw new Error(`System status failed: ${res.status}`);
        setSystemHealth(await res.json());
      } else {
        const res = await fetch("/api/v1/admin/audit-logs");
        if (!res.ok) throw new Error(`Audit logs failed: ${res.status}`);
        setAuditLogs(await res.json());
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
      const res = await fetch(`/api/v1/admin/users/${userId}/toggle-status`, {
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
    </div>
  );
};