"use client";

import React, { useMemo, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import {
  ChevronDown,
  ChevronRight,
  FolderKanban,
  Pencil,
  Trash2,
  Check,
  X,
  MessageSquare,
  Loader2,
  Plus,
} from "lucide-react";
import {
  createProject,
  deleteProject,
  moveConversationToProject,
  updateProject,
  type Conversation,
  type Project,
} from "@/lib/api";
import {
  conversationKeys,
  projectKeys,
  useConversations,
  useProjects,
} from "@/lib/queries";

export interface ProjectsViewProps {
  /**
   * Select a conversation in the chat tab. Optional so the view can be rendered
   * standalone (tests, storybook); without it the member rows are not clickable.
   */
  onOpenConversation?: (id: string) => void;
}

/**
 * Projects: lightweight, owner-scoped conversation groupings.
 *
 * The backend (`/projects` CRUD + `PATCH /conversations/{id}` with `project_id`)
 * and the BFF routes existed before this view did, but nothing in the app ever
 * called them — `fetchProjects` and friends were exported and unused, so the
 * feature was invisible and, in effect, absent. This is the missing surface.
 *
 * The project list is its own query (`projectKeys`). Membership is *not* a
 * second fetch: it is derived from the conversation list the chat shell already
 * caches, so a move patches that one cache entry and the two surfaces cannot
 * disagree. Mutations write their response straight into the cache rather than
 * refetching -- the API returns the authoritative row, and a refetch would race
 * a second one for the same data.
 */

export const ProjectsView: React.FC<ProjectsViewProps> = ({ onOpenConversation }) => {
  const queryClient = useQueryClient();
  const projectsQuery = useProjects();
  const conversationsQuery = useConversations(true);

  const projects = useMemo(() => projectsQuery.data ?? [], [projectsQuery.data]);
  const conversations = useMemo(
    () => conversationsQuery.data ?? [],
    [conversationsQuery.data],
  );

  const [newName, setNewName] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const [renamingId, setRenamingId] = useState<string | null>(null);
  const [renameValue, setRenameValue] = useState("");

  const membersOf = (projectId: string) =>
    conversations.filter((c) => c.project_id === projectId);

  const unassigned = useMemo(
    () => conversations.filter((c) => !c.project_id),
    [conversations],
  );

  const run = async (action: () => Promise<void>) => {
    setBusy(true);
    setError(null);
    try {
      await action();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Something went wrong");
    } finally {
      setBusy(false);
    }
  };

  const handleCreate = () => {
    const name = newName.trim();
    if (!name) return;
    void run(async () => {
      const created = await createProject(name);
      queryClient.setQueryData<Project[]>(projectKeys.all, (prev) => [
        created,
        ...(prev ?? []),
      ]);
      setNewName("");
    });
  };

  const startRename = (project: Project) => {
    setRenamingId(project.id);
    setRenameValue(project.name);
  };

  const handleRename = (project: Project) => {
    const name = renameValue.trim();
    setRenamingId(null);
    if (!name || name === project.name) return;
    void run(async () => {
      const updated = await updateProject(project.id, { name });
      queryClient.setQueryData<Project[]>(projectKeys.all, (prev) =>
        (prev ?? []).map((p) => (p.id === updated.id ? updated : p)),
      );
    });
  };

  const handleDelete = (project: Project) => {
    void run(async () => {
      await deleteProject(project.id);
      queryClient.setQueryData<Project[]>(projectKeys.all, (prev) =>
        (prev ?? []).filter((p) => p.id !== project.id),
      );
      if (expandedId === project.id) setExpandedId(null);
    });
  };

  // A move returns the updated conversation, so the chat shell's cache is
  // patched in place. The Projects view derives membership from that same entry.
  const handleMove = (conversationId: string, projectId: string | null) => {
    void run(async () => {
      const updated = await moveConversationToProject(conversationId, projectId);
      queryClient.setQueryData<Conversation[]>(conversationKeys.all, (prev) =>
        (prev ?? []).map((c) => (c.id === updated.id ? updated : c)),
      );
    });
  };

  const isLoading = projectsQuery.isPending;
  const loadError = projectsQuery.error
    ? projectsQuery.error instanceof Error
      ? projectsQuery.error.message
      : "Could not load projects"
    : null;

  return (
    <div className="mx-auto w-full max-w-3xl space-y-6 p-6">
      <header className="space-y-1">
        <h1 className="flex items-center gap-2 text-lg font-semibold text-[var(--text-primary)]">
          <FolderKanban className="h-5 w-5 text-[var(--accent)]" />
          Projects
        </h1>
        <p className="text-xs text-[var(--text-muted)]">
          Group related conversations. Move a conversation into a project from here,
          or remove it to send it back to your ungrouped chats.
        </p>
      </header>

      <div className="flex flex-col gap-2 sm:flex-row">
        <input
          aria-label="New project name"
          placeholder="New project name"
          value={newName}
          onChange={(e) => setNewName(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") {
              e.preventDefault();
              handleCreate();
            }
          }}
          maxLength={120}
          className="flex-1 rounded-lg border border-[var(--border-subtle)] bg-[var(--bg-surface)] px-3 py-2 text-sm text-[var(--text-primary)] outline-none focus:border-[var(--border-active)]"
        />
        <button
          onClick={handleCreate}
          disabled={busy || !newName.trim()}
          className="inline-flex items-center justify-center gap-1.5 rounded-lg border border-[var(--accent)] bg-[var(--accent-soft)] px-3 py-2 text-xs font-medium text-[var(--accent-ink)] transition-colors hover:opacity-90 disabled:opacity-50"
        >
          {busy ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Plus className="h-3.5 w-3.5" />}
          New project
        </button>
      </div>

      {(error || loadError) && (
        <div
          role="alert"
          className="flex items-center gap-2 rounded-lg border border-[var(--status-danger)]/40 bg-[var(--status-danger)]/10 px-3 py-2 text-xs text-[var(--status-danger)]"
        >
          <X className="h-3.5 w-3.5" />
          {error ?? loadError}
        </div>
      )}

      {isLoading ? (
        <div className="space-y-3" role="status" aria-busy="true">
          <span className="sr-only">Loading projects…</span>
          {Array.from({ length: 3 }, (_, i) => (
            <div
              key={i}
              className="h-14 animate-pulse rounded-xl border border-[var(--border-subtle)] bg-[var(--bg-surface)]"
            />
          ))}
        </div>
      ) : projects.length === 0 ? (
        <p className="rounded-xl border border-dashed border-[var(--border-subtle)] px-4 py-8 text-center text-sm text-[var(--text-muted)]">
          No projects yet. Create one to group related conversations.
        </p>
      ) : (
        <ul className="space-y-3">
          {projects.map((project) => {
            const members = membersOf(project.id);
            const isExpanded = expandedId === project.id;
            const isRenaming = renamingId === project.id;
            return (
              <li
                key={project.id}
                className="overflow-hidden rounded-xl border border-[var(--border-subtle)] bg-[var(--bg-surface)]"
              >
                <div className="flex items-center gap-2 px-3 py-2.5">
                  <button
                    type="button"
                    aria-label={`${isExpanded ? "Collapse" : "Expand"} ${project.name}`}
                    aria-expanded={isExpanded}
                    onClick={() => setExpandedId(isExpanded ? null : project.id)}
                    className="shrink-0 rounded p-0.5 text-[var(--text-muted)] hover:text-[var(--text-primary)]"
                  >
                    {isExpanded ? (
                      <ChevronDown className="h-4 w-4" />
                    ) : (
                      <ChevronRight className="h-4 w-4" />
                    )}
                  </button>

                  {isRenaming ? (
                    <input
                      autoFocus
                      aria-label={`Rename ${project.name} input`}
                      value={renameValue}
                      onChange={(e) => setRenameValue(e.target.value)}
                      onKeyDown={(e) => {
                        if (e.key === "Enter") {
                          e.preventDefault();
                          handleRename(project);
                        } else if (e.key === "Escape") {
                          e.preventDefault();
                          setRenamingId(null);
                        }
                      }}
                      maxLength={120}
                      className="flex-1 rounded border border-[var(--border-active)] bg-[var(--bg-base)] px-2 py-1 text-sm text-[var(--text-primary)] outline-none"
                    />
                  ) : (
                    <span className="flex-1 truncate text-sm text-[var(--text-primary)]">
                      {project.name}
                    </span>
                  )}

                  <span className="shrink-0 text-[11px] text-[var(--text-faint)]">
                    {members.length} {members.length === 1 ? "chat" : "chats"}
                  </span>

                  {isRenaming ? (
                    <>
                      <button
                        type="button"
                        aria-label={`Save name for ${project.name}`}
                        onClick={() => handleRename(project)}
                        className="shrink-0 rounded p-1 text-[var(--status-success)] hover:bg-[var(--bg-base)]"
                      >
                        <Check className="h-3.5 w-3.5" />
                      </button>
                      <button
                        type="button"
                        aria-label={`Cancel rename for ${project.name}`}
                        onClick={() => setRenamingId(null)}
                        className="shrink-0 rounded p-1 text-[var(--text-muted)] hover:bg-[var(--bg-base)]"
                      >
                        <X className="h-3.5 w-3.5" />
                      </button>
                    </>
                  ) : (
                    <>
                      <button
                        type="button"
                        aria-label={`Rename ${project.name}`}
                        onClick={() => startRename(project)}
                        className="shrink-0 rounded p-1 text-[var(--text-muted)] hover:bg-[var(--bg-base)] hover:text-[var(--text-primary)]"
                      >
                        <Pencil className="h-3.5 w-3.5" />
                      </button>
                      <button
                        type="button"
                        aria-label={`Delete ${project.name}`}
                        onClick={() => handleDelete(project)}
                        className="shrink-0 rounded p-1 text-[var(--text-muted)] hover:bg-[var(--bg-base)] hover:text-[var(--status-danger)]"
                      >
                        <Trash2 className="h-3.5 w-3.5" />
                      </button>
                    </>
                  )}
                </div>

                {isExpanded && (
                  <div className="space-y-1 border-t border-[var(--border-subtle)] px-3 py-2">
                    {members.length === 0 ? (
                      <p className="py-2 text-center text-[11px] text-[var(--text-faint)]">
                        No conversations in this project yet.
                      </p>
                    ) : (
                      members.map((c) => (
                        <div
                          key={c.id}
                          className="flex items-center gap-2 rounded-lg px-1 py-1 hover:bg-[var(--bg-base)]"
                        >
                          <MessageSquare className="h-3.5 w-3.5 shrink-0 text-[var(--text-muted)]" />
                          <button
                            type="button"
                            onClick={() => onOpenConversation?.(c.id)}
                            disabled={!onOpenConversation}
                            className="flex-1 truncate text-left text-xs text-[var(--text-primary)] disabled:cursor-default"
                          >
                            {c.title || "Untitled conversation"}
                          </button>
                          <button
                            type="button"
                            aria-label={`Remove ${c.title || "untitled"} from ${project.name}`}
                            onClick={() => handleMove(c.id, null)}
                            className="shrink-0 rounded p-1 text-[var(--text-muted)] hover:text-[var(--status-danger)]"
                          >
                            <X className="h-3.5 w-3.5" />
                          </button>
                        </div>
                      ))
                    )}

                    {unassigned.length > 0 && (
                      <select
                        aria-label={`Add conversation to ${project.name}`}
                        value=""
                        onChange={(e) => {
                          if (e.target.value) handleMove(e.target.value, project.id);
                        }}
                        className="mt-1 w-full rounded-lg border border-[var(--border-subtle)] bg-[var(--bg-base)] px-2 py-1.5 text-xs text-[var(--text-muted)] outline-none"
                      >
                        <option value="">Add a conversation…</option>
                        {unassigned.map((c) => (
                          <option key={c.id} value={c.id}>
                            {c.title || "Untitled conversation"}
                          </option>
                        ))}
                      </select>
                    )}
                  </div>
                )}
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
};
