"use client";

/**
 * Organizations and outbound webhooks — the two feature areas that previously
 * existed only behind `/api/v1/orgs` and `/api/v1/webhooks` with no UI at all,
 * so the docs described them as product features a user could never reach.
 *
 * Server state runs through the repo's query layer (`useOrganizations` /
 * `useWebhooks` + `lib/api`), so list loading, caching and invalidation behave
 * exactly like Settings/Knowledge/Projects; only the small per-row bits
 * (invite draft, expanded delivery table, one-time secrets) are local state.
 */

import React, { useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import {
  AlertCircle,
  Building2,
  ChevronDown,
  ChevronRight,
  Copy,
  Loader2,
  Plus,
  Trash2,
  Webhook,
} from "lucide-react";
import { toast } from "sonner";
import {
  acceptOrganizationInvite,
  createOrganization,
  createWebhook,
  deleteWebhook,
  fetchWebhookDeliveries,
  inviteOrganizationMember,
  leaveOrganization,
  setWebhookActive,
  type WebhookDelivery,
} from "@/lib/api";
import {
  orgKeys,
  useOrganizations,
  useWebhooks,
  webhookKeys,
} from "@/lib/queries";

const messageOf = (err: unknown, fallback: string) =>
  err instanceof Error && err.message ? err.message : fallback;

const cardClass =
  "rounded-xl border border-[var(--border-subtle)] bg-[var(--bg-surface)] p-4 space-y-4";
const inputClass =
  "flex-1 min-w-0 px-3 py-2 rounded-lg border border-[var(--border-subtle)] bg-[var(--bg-main)] text-xs text-[var(--text-primary)] placeholder:text-[var(--text-faint)] focus:outline-none focus:border-[var(--accent)]";
const buttonClass =
  "flex items-center gap-1.5 px-3 py-2 rounded-lg bg-[var(--accent)] hover:bg-[var(--accent-hover)] disabled:opacity-50 text-[var(--accent-foreground)] text-xs font-semibold transition-all";
const ghostButtonClass =
  "flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg border border-[var(--border-subtle)] hover:bg-[var(--bg-main)] disabled:opacity-50 text-[var(--text-secondary)] text-xs transition-all";

export const IntegrationsSettings: React.FC = () => {
  const queryClient = useQueryClient();
  const orgsQuery = useOrganizations();
  const hooksQuery = useWebhooks();

  const orgs = orgsQuery.data ?? [];
  const endpoints = hooksQuery.data ?? [];
  const orgError = orgsQuery.error
    ? messageOf(orgsQuery.error, "Could not load organizations")
    : null;
  const hookError = hooksQuery.error
    ? messageOf(hooksQuery.error, "Could not load webhooks")
    : null;

  // ── Organizations (local drafts) ──────────────────────────────────────────
  const [orgName, setOrgName] = useState("");
  const [orgBusy, setOrgBusy] = useState(false);
  const [inviteOrgId, setInviteOrgId] = useState<string | null>(null);
  const [inviteEmail, setInviteEmail] = useState("");
  const [inviteUrl, setInviteUrl] = useState<string | null>(null);
  const [acceptToken, setAcceptToken] = useState("");

  const refreshOrgs = () =>
    queryClient.invalidateQueries({ queryKey: orgKeys.all });

  const handleCreateOrg = async () => {
    const name = orgName.trim();
    if (!name || orgBusy) return;
    setOrgBusy(true);
    try {
      await createOrganization(name);
      setOrgName("");
      toast.success(`Organization "${name}" created`);
      await refreshOrgs();
    } catch (err) {
      toast.error(messageOf(err, "Could not create organization"));
    } finally {
      setOrgBusy(false);
    }
  };

  const handleInvite = async (orgId: string) => {
    const email = inviteEmail.trim();
    if (!email) return;
    try {
      const invite = await inviteOrganizationMember(orgId, email);
      setInviteUrl(invite.dev_accept_url ?? null);
      setInviteEmail("");
      toast.success(`Invite created for ${email}`);
    } catch (err) {
      toast.error(messageOf(err, "Could not create invite"));
    }
  };

  const handleAcceptInvite = async () => {
    const token = acceptToken.trim();
    if (!token) return;
    try {
      await acceptOrganizationInvite(token);
      setAcceptToken("");
      toast.success("Joined the organization");
      await refreshOrgs();
    } catch (err) {
      toast.error(messageOf(err, "Invite could not be accepted"));
    }
  };

  const handleLeave = async (name: string, orgId: string) => {
    try {
      await leaveOrganization(orgId);
      toast.success(`Left ${name}`);
      await refreshOrgs();
    } catch (err) {
      // Most common failure is legitimate: an owner cannot leave an org it
      // leads, and the backend says exactly that.
      toast.error(messageOf(err, "Could not leave organization"));
    }
  };

  // ── Webhooks (local drafts) ───────────────────────────────────────────────
  const [hookUrl, setHookUrl] = useState("");
  const [hookBusy, setHookBusy] = useState(false);
  const [newSecret, setNewSecret] = useState<string | null>(null);
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const [deliveries, setDeliveries] = useState<Record<string, WebhookDelivery[]>>({});
  const [deliveriesLoading, setDeliveriesLoading] = useState<string | null>(null);

  const refreshHooks = () =>
    queryClient.invalidateQueries({ queryKey: webhookKeys.all });

  const handleAddHook = async () => {
    const url = hookUrl.trim();
    if (!url || hookBusy) return;
    setHookBusy(true);
    try {
      const created = await createWebhook(url);
      setHookUrl("");
      // The signing secret is returned exactly once, at registration: showing
      // it here is the only chance the user gets to store it.
      setNewSecret(created.secret ?? null);
      toast.success("Webhook registered");
      await refreshHooks();
    } catch (err) {
      toast.error(messageOf(err, "Could not register webhook"));
    } finally {
      setHookBusy(false);
    }
  };

  const handleToggleHook = async (id: string, isActive: boolean) => {
    try {
      await setWebhookActive(id, isActive);
      await refreshHooks();
    } catch (err) {
      toast.error(messageOf(err, "Could not update webhook"));
    }
  };

  const handleDeleteHook = async (id: string) => {
    try {
      await deleteWebhook(id);
      toast.success("Webhook removed");
      await refreshHooks();
    } catch (err) {
      toast.error(messageOf(err, "Could not remove webhook"));
    }
  };

  const toggleDeliveries = async (id: string) => {
    if (expandedId === id) {
      setExpandedId(null);
      return;
    }
    setExpandedId(id);
    if (deliveries[id]) return;
    setDeliveriesLoading(id);
    try {
      const rows = await fetchWebhookDeliveries(id);
      setDeliveries((prev) => ({ ...prev, [id]: rows }));
    } catch {
      setDeliveries((prev) => ({ ...prev, [id]: [] }));
    } finally {
      setDeliveriesLoading(null);
    }
  };

  const copy = async (value: string) => {
    try {
      await navigator.clipboard.writeText(value);
      toast.success("Copied to clipboard");
    } catch {
      toast.error("Could not access the clipboard");
    }
  };

  return (
    <div className="space-y-6">
      {/* Organizations */}
      <section className={cardClass} aria-label="Organizations">
        <div className="flex items-center justify-between">
          <h3 className="text-sm font-semibold text-[var(--text-primary)] flex items-center gap-2">
            <Building2 className="w-4 h-4 text-[var(--accent-ink)]" /> Organizations
          </h3>
          <button
            onClick={() => void orgsQuery.refetch()}
            className="text-[11px] text-[var(--text-faint)] hover:text-[var(--text-primary)]"
          >
            Refresh
          </button>
        </div>

        <p className="text-[11px] text-[var(--text-muted)]">
          Shared workspaces: invite colleagues by email, then hand them the accept
          link.
        </p>

        {orgError && (
          <p className="flex items-center gap-1.5 text-[11px] text-[var(--status-danger)]">
            <AlertCircle className="w-3.5 h-3.5" /> {orgError}
          </p>
        )}

        <div className="flex gap-2">
          <input
            className={inputClass}
            placeholder="New organization name"
            aria-label="New organization name"
            value={orgName}
            onChange={(e) => setOrgName(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") void handleCreateOrg();
            }}
          />
          <button
            onClick={() => void handleCreateOrg()}
            disabled={orgBusy || !orgName.trim()}
            className={buttonClass}
          >
            {orgBusy ? (
              <Loader2 className="w-3.5 h-3.5 animate-spin" />
            ) : (
              <Plus className="w-3.5 h-3.5" />
            )}
            Create
          </button>
        </div>

        {orgsQuery.isPending ? (
          <p className="text-[11px] text-[var(--text-faint)]">Loading…</p>
        ) : orgs.length === 0 ? (
          <p className="text-[11px] text-[var(--text-faint)]">
            No organizations yet. Create one to share conversations with a team.
          </p>
        ) : (
          <ul className="space-y-2">
            {orgs.map((org) => (
              <li
                key={org.id}
                className="p-3 rounded-lg border border-[var(--border-subtle)] bg-[var(--bg-main)] text-xs space-y-2"
              >
                <div className="flex items-center justify-between gap-2">
                  <div className="min-w-0">
                    <span className="font-medium text-[var(--text-primary)]">
                      {org.name}
                    </span>
                    <span className="ml-2 text-[9px] uppercase px-1.5 py-0.5 rounded bg-[var(--accent-soft)] text-[var(--accent-ink)] font-mono">
                      {org.role}
                    </span>
                  </div>
                  <div className="flex items-center gap-1.5 shrink-0">
                    <button
                      onClick={() =>
                        setInviteOrgId(inviteOrgId === org.id ? null : org.id)
                      }
                      className={ghostButtonClass}
                    >
                      Invite
                    </button>
                    <button
                      onClick={() => void handleLeave(org.name, org.id)}
                      className={ghostButtonClass}
                    >
                      Leave
                    </button>
                  </div>
                </div>

                {inviteOrgId === org.id && (
                  <div className="flex gap-2">
                    <input
                      className={inputClass}
                      type="email"
                      placeholder="colleague@example.com"
                      aria-label="Invitee email"
                      value={inviteEmail}
                      onChange={(e) => setInviteEmail(e.target.value)}
                      onKeyDown={(e) => {
                        if (e.key === "Enter") void handleInvite(org.id);
                      }}
                    />
                    <button
                      onClick={() => void handleInvite(org.id)}
                      disabled={!inviteEmail.trim()}
                      className={ghostButtonClass}
                    >
                      Create invite
                    </button>
                  </div>
                )}
              </li>
            ))}
          </ul>
        )}

        {inviteUrl && (
          <div className="p-3 rounded-lg border border-[var(--accent)]/40 bg-[var(--accent-soft)] text-[11px] space-y-1.5">
            <p className="text-[var(--accent-ink)] font-medium">
              Send this link to your invitee:
            </p>
            <div className="flex items-center gap-2">
              <code className="flex-1 min-w-0 truncate font-mono text-[var(--text-secondary)]">
                {inviteUrl}
              </code>
              <button onClick={() => void copy(inviteUrl)} className={ghostButtonClass}>
                <Copy className="w-3 h-3" /> Copy
              </button>
              <button
                onClick={() => setInviteUrl(null)}
                className="text-[var(--text-faint)] hover:text-[var(--text-primary)]"
              >
                Dismiss
              </button>
            </div>
          </div>
        )}

        <div className="flex gap-2 pt-2 border-t border-[var(--border-subtle)]">
          <input
            className={inputClass}
            placeholder="Paste an invite token to join"
            aria-label="Invite token"
            value={acceptToken}
            onChange={(e) => setAcceptToken(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") void handleAcceptInvite();
            }}
          />
          <button
            onClick={() => void handleAcceptInvite()}
            disabled={!acceptToken.trim()}
            className={ghostButtonClass}
          >
            Join
          </button>
        </div>
      </section>

      {/* Webhooks */}
      <section className={cardClass} aria-label="Webhooks">
        <div className="flex items-center justify-between">
          <h3 className="text-sm font-semibold text-[var(--text-primary)] flex items-center gap-2">
            <Webhook className="w-4 h-4 text-[var(--accent-ink)]" /> Webhooks
          </h3>
          <button
            onClick={() => void hooksQuery.refetch()}
            className="text-[11px] text-[var(--text-faint)] hover:text-[var(--text-primary)]"
          >
            Refresh
          </button>
        </div>

        <p className="text-[11px] text-[var(--text-muted)]">
          Signed POSTs to your own endpoint when a run completes. Private and
          internal addresses are rejected (SSRF guard), and each delivery is
          retried with a bounded attempt count.
        </p>

        {hookError && (
          <p className="flex items-center gap-1.5 text-[11px] text-[var(--status-danger)]">
            <AlertCircle className="w-3.5 h-3.5" /> {hookError}
          </p>
        )}

        <div className="flex gap-2">
          <input
            className={inputClass}
            placeholder="https://your-server.com/hooks/nexus"
            aria-label="Webhook URL"
            value={hookUrl}
            onChange={(e) => setHookUrl(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") void handleAddHook();
            }}
          />
          <button
            onClick={() => void handleAddHook()}
            disabled={hookBusy || !hookUrl.trim()}
            className={buttonClass}
          >
            {hookBusy ? (
              <Loader2 className="w-3.5 h-3.5 animate-spin" />
            ) : (
              <Plus className="w-3.5 h-3.5" />
            )}
            Add endpoint
          </button>
        </div>

        {newSecret && (
          <div className="p-3 rounded-lg border border-[var(--accent)]/40 bg-[var(--accent-soft)] text-[11px] space-y-1.5">
            <p className="text-[var(--accent-ink)] font-medium">
              Signing secret — shown once, store it now:
            </p>
            <div className="flex items-center gap-2">
              <code className="flex-1 min-w-0 truncate font-mono text-[var(--text-secondary)]">
                {newSecret}
              </code>
              <button onClick={() => void copy(newSecret)} className={ghostButtonClass}>
                <Copy className="w-3 h-3" /> Copy
              </button>
              <button
                onClick={() => setNewSecret(null)}
                className="text-[var(--text-faint)] hover:text-[var(--text-primary)]"
              >
                Dismiss
              </button>
            </div>
          </div>
        )}

        {hooksQuery.isPending ? (
          <p className="text-[11px] text-[var(--text-faint)]">Loading…</p>
        ) : endpoints.length === 0 ? (
          <p className="text-[11px] text-[var(--text-faint)]">
            No endpoints registered yet.
          </p>
        ) : (
          <ul className="space-y-2">
            {endpoints.map((ep) => (
              <li
                key={ep.id}
                className="p-3 rounded-lg border border-[var(--border-subtle)] bg-[var(--bg-main)] text-xs space-y-2"
              >
                <div className="flex items-center justify-between gap-2">
                  <button
                    onClick={() => void toggleDeliveries(ep.id)}
                    className="flex items-center gap-1.5 min-w-0 text-left"
                    title="Show delivery history"
                  >
                    {expandedId === ep.id ? (
                      <ChevronDown className="w-3.5 h-3.5 text-[var(--text-faint)]" />
                    ) : (
                      <ChevronRight className="w-3.5 h-3.5 text-[var(--text-faint)]" />
                    )}
                    <span className="truncate font-mono text-[var(--text-primary)]">
                      {ep.url}
                    </span>
                  </button>
                  <div className="flex items-center gap-1.5 shrink-0">
                    <button
                      onClick={() => void handleToggleHook(ep.id, !ep.is_active)}
                      className={ghostButtonClass}
                      title={ep.is_active ? "Deactivate" : "Activate"}
                    >
                      {ep.is_active ? "Active" : "Paused"}
                    </button>
                    <button
                      onClick={() => void handleDeleteHook(ep.id)}
                      className="text-[var(--text-faint)] hover:text-[var(--status-danger)] p-1"
                      title="Delete endpoint"
                      aria-label={`Delete webhook ${ep.url}`}
                    >
                      <Trash2 className="w-3.5 h-3.5" />
                    </button>
                  </div>
                </div>
                <p className="text-[10px] text-[var(--text-faint)] font-mono">
                  {ep.events.join(", ")}
                  {ep.has_secret ? " · signed" : ""}
                </p>

                {expandedId === ep.id && (
                  <div className="pt-2 border-t border-[var(--border-subtle)]">
                    {deliveriesLoading === ep.id ? (
                      <p className="text-[11px] text-[var(--text-faint)]">Loading…</p>
                    ) : !deliveries[ep.id] || deliveries[ep.id].length === 0 ? (
                      <p className="text-[11px] text-[var(--text-faint)]">
                        No deliveries yet — one is queued when a run completes.
                      </p>
                    ) : (
                      <table className="w-full text-[10px]">
                        <thead>
                          <tr className="text-[var(--text-faint)] text-left">
                            <th className="py-1 font-medium">Event</th>
                            <th className="py-1 font-medium">Status</th>
                            <th className="py-1 font-medium">HTTP</th>
                            <th className="py-1 font-medium">Tries</th>
                            <th className="py-1 font-medium">When</th>
                          </tr>
                        </thead>
                        <tbody>
                          {deliveries[ep.id].map((d) => (
                            <tr
                              key={d.id}
                              className="border-t border-[var(--border-subtle)] text-[var(--text-secondary)]"
                            >
                              <td className="py-1 font-mono">{d.event}</td>
                              <td className="py-1">{d.status}</td>
                              <td className="py-1">{d.http_status ?? "—"}</td>
                              <td className="py-1">{d.attempts}</td>
                              <td className="py-1">
                                {d.created_at
                                  ? new Date(d.created_at).toLocaleString()
                                  : "—"}
                              </td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    )}
                  </div>
                )}
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  );
};
