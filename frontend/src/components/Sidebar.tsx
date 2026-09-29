"use client";

import React from "react";
import {
  MessageSquare,
  Plus,
  FileText,
  Sliders,
  BarChart2,
  Trash2,
  Cpu,
  Pin,
  Sparkles,
  LogOut,
  GitFork,
  Pencil,
  Check,
  X,
  Search,
} from "lucide-react";
import { MODELS } from "@/lib/models";

export interface ConversationItem {
  id: string;
  title: string;
  model: string;
  is_pinned: boolean;
  updated_at: string;
}

interface SidebarProps {
  conversations: ConversationItem[];
  activeConversationId: string | null;
  onSelectConversation: (id: string) => void;
  onNewChat: () => void;
  onDeleteConversation: (id: string, e: React.MouseEvent) => void;
  onForkConversation?: (id: string, e: React.MouseEvent) => void;
  /**
   * Persists a new title. Optional so a caller that does not allow renaming can
   * omit it and the pencil button is not rendered.
   */
  onRenameConversation?: (id: string, title: string) => Promise<void> | void;
  activeTab: "chat" | "files" | "settings" | "usage" | "admin";
  setActiveTab: (tab: "chat" | "files" | "settings" | "usage" | "admin") => void;
  currentModel: string;
  onChangeModel: (model: string) => void;
  onSignOut?: () => void;
  /** When false (the default) the Admin nav item is hidden entirely. */
  showAdmin?: boolean;
  /** Opens the ⌘K command palette; the search trigger renders only when provided. */
  onOpenCommandPalette?: () => void;
}

interface NavItem {
  tab: SidebarProps["activeTab"];
  label: string;
  Icon: React.ComponentType<{ className?: string }>;
}

export const Sidebar: React.FC<SidebarProps> = ({
  conversations,
  activeConversationId,
  onSelectConversation,
  onNewChat,
  onDeleteConversation,
  onForkConversation,
  onRenameConversation,
  activeTab,
  setActiveTab,
  currentModel,
  onChangeModel,
  onSignOut,
  showAdmin = false,
  onOpenCommandPalette,
}: SidebarProps) => {
  const models = MODELS;

  const pinnedList = conversations.filter((c) => c.is_pinned);
  const recentList = conversations.filter((c) => !c.is_pinned);

  // Inline rename state. `editingId` tracks which row is in edit mode and
  // `editingTitle` holds the in-progress text, so a rename never round-trips
  // through the server until the user commits it.
  const [editingId, setEditingId] = React.useState<string | null>(null);
  const [editingTitle, setEditingTitle] = React.useState("");

  const beginRename = (c: ConversationItem) => {
    setEditingId(c.id);
    setEditingTitle(c.title);
  };

  const cancelRename = () => {
    setEditingId(null);
    setEditingTitle("");
  };

  const commitRename = async (c: ConversationItem) => {
    const next = editingTitle.trim();
    // Reset the editor first: a rejected save should still leave the row
    // editable rather than stuck in a mode the user cannot get out of.
    cancelRename();
    if (!next || next === c.title) return;
    try {
      await onRenameConversation?.(c.id, next);
    } catch {
      // The parent re-throws only to surface a toast; the list is reloaded
      // from the server on failure so the title snaps back.
    }
  };

  const navItems: NavItem[] = [
    { tab: "files", label: "Knowledge", Icon: FileText },
    { tab: "usage", label: "Usage", Icon: BarChart2 },
    { tab: "settings", label: "Settings", Icon: Sliders },
    // Admin is only visible to admins; non-admins who somehow land here are
    // bounced back to chat by the parent page.
    ...(showAdmin ? [{ tab: "admin" as const, label: "Admin", Icon: Sparkles }] : []),
  ];

  const renderConversation = (c: ConversationItem) => (
    <div
      key={c.id}
      onClick={() => onSelectConversation(c.id)}
      className={`group flex items-center justify-between px-3 py-2 rounded-lg text-xs cursor-pointer transition-colors ${
        activeConversationId === c.id
          ? "bg-[var(--accent-soft)] text-[var(--accent-ink)] border border-[var(--accent)]"
          : "text-[var(--text-muted)] hover:bg-[var(--bg-surface)] hover:text-[var(--text-primary)] border border-transparent"
      }`}
    >
      <div className="flex items-center gap-2 truncate">
        <MessageSquare className="w-3.5 h-3.5 shrink-0 opacity-70" />
        {editingId === c.id ? (
          <input
            // Autofocus is deliberate: the field only exists while the user is
            // renaming, so it takes focus from nothing.
            autoFocus
            aria-label={`Rename ${c.title}`}
            value={editingTitle}
            onChange={(e) => setEditingTitle(e.target.value)}
            onClick={(e) => e.stopPropagation()}
            onBlur={() => void commitRename(c)}
            onKeyDown={(e) => {
              if (e.key === "Enter") {
                e.preventDefault();
                void commitRename(c);
              } else if (e.key === "Escape") {
                e.preventDefault();
                cancelRename();
              }
            }}
            maxLength={200}
            className="w-full bg-[var(--bg-surface)] border border-[var(--border-active)] rounded px-1.5 py-0.5 text-xs text-[var(--text-primary)] outline-none"
          />
        ) : (
          <span className="truncate">{c.title}</span>
        )}
      </div>
      <div className="flex items-center gap-1">
        {editingId === c.id ? (
          <>
            <button
              onClick={(e) => {
                e.stopPropagation();
                void commitRename(c);
              }}
              title="Save name"
              className="text-[var(--accent)] p-0.5"
            >
              <Check className="w-3.5 h-3.5" />
            </button>
            <button
              onClick={(e) => {
                e.stopPropagation();
                cancelRename();
              }}
              title="Cancel rename"
              className="text-[var(--text-faint)] hover:text-[var(--text-primary)] p-0.5"
            >
              <X className="w-3.5 h-3.5" />
            </button>
          </>
        ) : (
          <>
        {onRenameConversation && (
          <button
            onClick={(e) => {
              e.stopPropagation();
              beginRename(c);
            }}
            title="Rename conversation"
            aria-label={`Rename ${c.title}`}
            className="opacity-0 group-hover:opacity-100 text-[var(--text-faint)] hover:text-[var(--accent)] p-0.5 transition-opacity"
          >
            <Pencil className="w-3.5 h-3.5" />
          </button>
        )}
        {onForkConversation && (
          <button
            onClick={(e) => onForkConversation(c.id, e)}
            title="Fork/Branch conversation"
            className="opacity-0 group-hover:opacity-100 text-[var(--text-faint)] hover:text-[var(--accent-ink)] p-0.5 transition-opacity"
          >
            <GitFork className="w-3.5 h-3.5" />
          </button>
        )}
        <button
          onClick={(e) => onDeleteConversation(c.id, e)}
          title="Delete conversation"
          className="opacity-0 group-hover:opacity-100 text-[var(--text-faint)] hover:text-[var(--status-danger)] p-0.5 transition-opacity"
        >
          <Trash2 className="w-3.5 h-3.5" />
        </button>
          </>
        )}
      </div>
    </div>
  );

  return (
    <aside className="w-72 h-screen flex flex-col bg-[var(--bg-main)] border-r border-[var(--border-subtle)] select-none">
      {/* Brand Header */}
      <div className="p-4 flex items-center justify-between border-b border-[var(--border-subtle)]">
        <div className="flex items-center gap-2.5">
          <div className="w-8 h-8 rounded-lg bg-[var(--bg-surface-elevated)] border border-[var(--border-subtle)] flex items-center justify-center">
            <Sparkles className="w-4 h-4 text-[var(--accent-ink)]" />
          </div>
          <div>
            <h1 className="font-semibold text-sm tracking-tight text-[var(--text-primary)]">
              Nexus AI
            </h1>
            <span className="text-[10px] text-[var(--text-faint)] font-medium tracking-wider uppercase">
              Agentic assistant
            </span>
          </div>
        </div>
        {onSignOut && (
          <button
            onClick={onSignOut}
            title="Sign out"
            className="p-1.5 rounded-lg text-[var(--text-faint)] hover:text-[var(--status-danger)] hover:bg-[var(--bg-surface)] transition-colors"
          >
            <LogOut className="w-4 h-4" />
          </button>
        )}
      </div>

      {/* Command palette trigger */}
      {onOpenCommandPalette && (
        <div className="px-3 pt-3">
          <button
            type="button"
            onClick={onOpenCommandPalette}
            aria-label="Search or run a command (Command or Control K)"
            className="flex w-full items-center gap-2 rounded-lg border border-[var(--border-subtle)] bg-[var(--bg-surface)] px-3 py-2 text-xs text-[var(--text-muted)] transition-colors hover:border-[var(--border-strong)] hover:text-[var(--text-secondary)]"
          >
            <Search className="h-3.5 w-3.5" aria-hidden="true" />
            <span>Search or run a command</span>
            <kbd className="ml-auto rounded border border-[var(--border-subtle)] px-1.5 py-0.5 font-mono text-[10px] text-[var(--text-faint)]">
              ⌘K
            </kbd>
          </button>
        </div>
      )}

      {/* Model Selector */}
      <div className="px-3 pt-3">
        <div className="p-2.5 rounded-xl border border-[var(--border-subtle)] bg-[var(--bg-surface)]">
          <div className="flex items-center justify-between mb-1.5 px-1">
            <span className="text-[11px] text-[var(--text-muted)] font-medium flex items-center gap-1.5">
              <Cpu className="w-3 h-3 text-[var(--accent-ink)]" /> Model
            </span>
          </div>
          <select
            value={currentModel}
            onChange={(e) => onChangeModel(e.target.value)}
            className="w-full rounded-lg border border-[var(--border-subtle)] bg-[var(--bg-main)] text-xs text-[var(--text-secondary)] px-2.5 py-1.5 outline-none focus:border-[var(--accent)] transition-colors"
          >
            {models.map((m) => (
              <option key={m.id} value={m.id}>
                {m.name} ({m.provider})
              </option>
            ))}
          </select>
        </div>
      </div>

      {/* New Chat Button */}
      <div className="p-3">
        <button
          onClick={onNewChat}
          className="w-full flex items-center justify-center gap-2 py-2.5 px-4 rounded-lg bg-[var(--accent)] hover:bg-[var(--accent-hover)] text-[var(--accent-foreground)] text-xs font-semibold shadow-sm transition-all active:scale-[0.98]"
        >
          <Plus className="w-4 h-4" />
          <span>New conversation</span>
        </button>
      </div>

      {/* Conversation History */}
      <div className="flex-1 overflow-y-auto px-3 py-2 space-y-4">
        {pinnedList.length > 0 && (
          <div>
            <div className="text-[11px] font-medium text-[var(--text-faint)] px-2 mb-1.5 flex items-center gap-1">
              <Pin className="w-3 h-3" /> Pinned
            </div>
            <div className="space-y-1">{pinnedList.map(renderConversation)}</div>
          </div>
        )}

        <div>
          <div className="text-[11px] font-medium text-[var(--text-faint)] px-2 mb-1.5">
            Recent conversations
          </div>
          <div className="space-y-1">{recentList.map(renderConversation)}</div>
          {recentList.length === 0 && (
            <p className="text-[11px] text-[var(--text-faint)] px-2 py-3 text-center">
              No recent conversations
            </p>
          )}
        </div>
      </div>

      {/* Bottom Navigation */}
      <div className="p-3 border-t border-[var(--border-subtle)] space-y-1">
        {navItems.map(({ tab, label, Icon }) => (
          <button
            key={tab}
            onClick={() => setActiveTab(tab)}
            className={`w-full flex items-center gap-2.5 px-3 py-2 rounded-lg text-xs transition-colors ${
              activeTab === tab
                ? "bg-[var(--accent-soft)] text-[var(--accent-ink)] border border-[var(--accent)]"
                : "text-[var(--text-muted)] hover:bg-[var(--bg-surface)] hover:text-[var(--text-primary)] border border-transparent"
            }`}
          >
            <Icon className="w-4 h-4" />
            <span>{label}</span>
          </button>
        ))}
      </div>
    </aside>
  );
};