"use client";

import { useQuery } from "@tanstack/react-query";
import {
  fetchAPIKeys,
  fetchArtifacts,
  fetchAuditReport,
  fetchBanditStatus,
  fetchConversations,
  fetchFairnessReport,
  fetchHookPolicies,
  fetchKnowledgeFiles,
  fetchMemories,
  fetchOptimizationRuns,
  fetchPlans,
  fetchProjects,
  fetchRedTeamRuns,
  fetchSettings,
  fetchSliceReport,
  fetchUsage,
  type APIKey,
  type ArtifactItem,
  type Conversation,
  type KnowledgeFile,
  type Project,
} from "@/lib/api";

// ─── Admin-surface types ─────────────────────────────────────────────────────
//
// These three were declared inside AdminView, which meant the query layer could
// not type its own cache (and the shapes were re-declared by every consumer that
// needed them). They live here now and AdminView imports them back, so there is
// one definition of each.

export interface AdminUser {
  id: string;
  email: string;
  username: string;
  full_name: string | null;
  role: string;
  is_active: boolean;
  created_at: string | null;
}

export interface AuditLogItem {
  id: string;
  action: string;
  resource_type: string;
  status: string;
  ip_address: string | null;
  created_at: string | null;
}

export interface SystemHealth {
  status: string;
  database: string;
  redis_cache: string;
}

// ─── Query keys ──────────────────────────────────────────────────────────────

export const conversationKeys = {
  all: ["conversations"] as const,
};

export const artifactKeys = {
  byConversation: (conversationId: string) => ["artifacts", conversationId] as const,
};

export const planKeys = {
  byConversation: (conversationId: string) => ["plans", conversationId] as const,
};

export const knowledgeKeys = {
  all: ["knowledge", "files"] as const,
};

export const projectKeys = {
  all: ["projects"] as const,
};

export const usageKeys = {
  all: ["usage", "summary"] as const,
};

export const settingsKeys = {
  settings: ["settings"] as const,
  memories: ["settings", "memories"] as const,
  keys: ["settings", "keys"] as const,
};

export const adminKeys = {
  users: ["admin", "users"] as const,
  systemStatus: ["admin", "system-status"] as const,
  auditLogs: ["admin", "audit-logs"] as const,
  hooks: ["admin", "hooks"] as const,
  slices: ["admin", "monitoring", "slices"] as const,
  fairness: ["admin", "monitoring", "fairness"] as const,
  bandits: ["admin", "monitoring", "bandits"] as const,
  optimizationRuns: ["admin", "optimization", "runs"] as const,
  auditReport: ["admin", "audit"] as const,
  redTeamRuns: ["admin", "audit", "redteam"] as const,
};

// ─── App shell: conversations, artifacts, plans ──────────────────────────────

/**
 * The conversation list, stored in the cache as a normalized `Conversation[]`
 * rather than the `ConversationPage` the api returns. Every optimistic
 * `setQueryData` in the page therefore operates on an array, and there is one
 * shape in the cache no matter which caller last touched it.
 */
export function useConversations(enabled: boolean) {
  return useQuery({
    queryKey: conversationKeys.all,
    queryFn: async (): Promise<Conversation[]> => (await fetchConversations(1, 50)).items,
    enabled,
  });
}

/** A saved artifact as the canvas wants it: list rows are the active version. */
export type CachedArtifact = ArtifactItem & { isActiveVersion: boolean };

export function useArtifacts(
  conversationId: string | null | undefined,
  enabled: boolean,
) {
  return useQuery({
    queryKey: artifactKeys.byConversation(conversationId ?? ""),
    queryFn: async (): Promise<CachedArtifact[]> => {
      const list = await fetchArtifacts(conversationId);
      // The server has no `isActiveVersion` field — ArtifactResponse carries
      // only `version` — and the canvas uses the flag to choose between the
      // "Persisted version N • saved" badge and the "Snapshot" one. The list
      // endpoint returns each artifact's current version, so it is the active
      // version by definition.
      return Array.isArray(list)
        ? list.map((a) => ({ ...a, isActiveVersion: true }))
        : [];
    },
    enabled: enabled && !!conversationId,
  });
}

export function usePlans(
  conversationId: string | null | undefined,
  enabled: boolean,
) {
  return useQuery({
    queryKey: planKeys.byConversation(conversationId ?? ""),
    queryFn: () => fetchPlans(conversationId as string),
    enabled: enabled && !!conversationId,
  });
}

// ─── Projects ────────────────────────────────────────────────────────────────

/**
 * Owner-scoped conversation groupings (backend `GET /projects`).
 *
 * The list is the only server state the Projects view owns; membership is
 * derived from the conversation list (which the chat shell already caches
 * under `conversationKeys.all`), so moving a conversation invalidates that
 * key rather than this one and the two surfaces never disagree.
 */
export function useProjects() {
  return useQuery({
    queryKey: projectKeys.all,
    queryFn: (): Promise<Project[]> => fetchProjects(),
  });
}

// ─── Knowledge ───────────────────────────────────────────────────────────────

/**
 * Indexed documents.
 *
 * The poll that used to live in a `setTimeout` effect is expressed as a
 * `refetchInterval` predicate: four seconds while any file is still pending or
 * processing, and `false` the moment every file reaches a terminal state. The
 * predicate reads the freshly returned data, so it stops on the first response
 * that needs no more polling — the same behaviour, without a timer that could
 * outlive the component.
 */
export function useKnowledgeFiles() {
  return useQuery({
    queryKey: knowledgeKeys.all,
    queryFn: () => fetchKnowledgeFiles(),
    refetchInterval: (query) => {
      const files: KnowledgeFile[] | undefined = query.state.data;
      const hasPending = (files ?? []).some(
        (f) => f.status === "pending" || f.status === "processing",
      );
      return hasPending ? 4000 : false;
    },
  });
}

// ─── Usage ───────────────────────────────────────────────────────────────────

export function useUsage() {
  return useQuery({ queryKey: usageKeys.all, queryFn: fetchUsage });
}

// ─── Settings ────────────────────────────────────────────────────────────────

export function useSettingsQuery() {
  return useQuery({ queryKey: settingsKeys.settings, queryFn: fetchSettings });
}

export function useMemoriesQuery() {
  return useQuery({ queryKey: settingsKeys.memories, queryFn: fetchMemories });
}

export function useAPIKeysQuery() {
  return useQuery({ queryKey: settingsKeys.keys, queryFn: fetchAPIKeys });
}

/**
 * Merge a saved API key into the cached list.
 *
 * The backend stores one key per (user, provider) and, when a provider is
 * re-added, updates that row in place and returns its *existing* id (verified
 * against the live endpoint). An unconditional prepend therefore put two cache
 * entries behind one id: React warned about duplicate keys, a single delete
 * removed both, and the live list fell from two rows to zero. Replace by id.
 */
export function mergeAPIKey(
  keys: APIKey[] | undefined,
  created: APIKey,
): APIKey[] {
  return [created, ...(keys ?? []).filter((k) => k.id !== created.id)];
}

// ─── Admin ───────────────────────────────────────────────────────────────────
//
// Each tab is its own query, enabled only while that tab is active, so switching
// tabs fetches just the data it needs and a slow report cannot block the users
// list. Wrapped in an arrow rather than passed by reference: the api helpers
// take an optional `retry` argument, and React Query calls `queryFn` with its
// own context object — handed straight through, that context would arrive as
// `retry`.

export function useAdminUsers(enabled: boolean) {
  return useQuery({
    queryKey: adminKeys.users,
    queryFn: async (): Promise<AdminUser[]> => {
      const res = await fetch("/api/admin/users");
      if (!res.ok) throw new Error(`Users endpoint failed: ${res.status}`);
      return res.json();
    },
    enabled,
  });
}

export function useSystemHealth(enabled: boolean) {
  return useQuery({
    queryKey: adminKeys.systemStatus,
    queryFn: async (): Promise<SystemHealth> => {
      const res = await fetch("/api/admin/system-status");
      if (!res.ok) throw new Error(`System status failed: ${res.status}`);
      return res.json();
    },
    enabled,
  });
}

export function useAuditLogs(enabled: boolean) {
  return useQuery({
    queryKey: adminKeys.auditLogs,
    queryFn: async (): Promise<AuditLogItem[]> => {
      const res = await fetch("/api/admin/audit-logs");
      if (!res.ok) throw new Error(`Audit logs failed: ${res.status}`);
      return res.json();
    },
    enabled,
  });
}

export function useHookPolicies(enabled: boolean) {
  return useQuery({
    queryKey: adminKeys.hooks,
    queryFn: () => fetchHookPolicies(),
    enabled,
  });
}

export function useSliceReport(enabled: boolean) {
  return useQuery({
    queryKey: adminKeys.slices,
    queryFn: () => fetchSliceReport(),
    enabled,
  });
}

export function useFairnessReport(enabled: boolean) {
  return useQuery({
    queryKey: adminKeys.fairness,
    queryFn: () => fetchFairnessReport(),
    enabled,
  });
}

export function useBanditStatus(enabled: boolean) {
  return useQuery({
    queryKey: adminKeys.bandits,
    queryFn: () => fetchBanditStatus(),
    enabled,
  });
}

export function useOptimizationRuns(enabled: boolean) {
  return useQuery({
    queryKey: adminKeys.optimizationRuns,
    queryFn: () => fetchOptimizationRuns(),
    enabled,
  });
}

export function useAuditReport(enabled: boolean) {
  return useQuery({
    queryKey: adminKeys.auditReport,
    queryFn: () => fetchAuditReport(),
    enabled,
  });
}

export function useRedTeamRuns(enabled: boolean) {
  return useQuery({
    queryKey: adminKeys.redTeamRuns,
    queryFn: () => fetchRedTeamRuns(),
    enabled,
  });
}
