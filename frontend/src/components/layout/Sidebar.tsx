"use client";

import React from "react";
import {
  MessageSquare,
  Plus,
  FileText,
  FolderKanban,
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
  Share2,
  Archive,
  ArchiveRestore,
  ChevronDown,
  ChevronRight,
  PanelLeftClose,
  PanelLeftOpen,
  PanelRight,
} from "lucide-react";
import Image from "next/image";
import { MODEL_CATALOG } from "@/lib/models";
import type { SidebarDock } from "@/lib/sidebar-layout";

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
   * Mint + copy a read-only share link for one conversation. Optional so a
   * caller without sharing enabled simply omits the button.
   */
  onShareConversation?: (id: string, e: React.MouseEvent) => void;
  /**
   * Persists a new title. Optional so a caller that does not allow renaming can
   * omit it and the pencil button is not rendered.
   */
  onRenameConversation?: (id: string, title: string) => Promise<void> | void;
  /** Toggle a conversation's pinned state; parent owns the server call. */
  onPinConversation?: (id: string, pinned: boolean) => Promise<void> | void;
  /**
   * Move a conversation out of the sidebar without deleting it. Optional so a
   * caller that does not support archiving simply omits the button.
   */
  onArchiveConversation?: (id: string, e: React.MouseEvent) => void;
  /** Archived conversations, shown only while the archived list is expanded. */
  archivedConversations?: ConversationItem[];
  showArchived?: boolean;
  onToggleArchived?: () => void;
  onRestoreConversation?: (id: string) => void;
  archivedLoading?: boolean;
  activeTab: "chat" | "files" | "projects" | "settings" | "usage" | "admin";
  setActiveTab: (tab: "chat" | "files" | "projects" | "settings" | "usage" | "admin") => void;
  currentModel: string;
  onChangeModel: (model: string) => void;
  onSignOut?: () => void;
  /** When false (the default) the Admin nav item is hidden entirely. */
  showAdmin?: boolean;
  /** Opens the ⌘K command palette; the search trigger renders only when provided. */
  onOpenCommandPalette?: () => void;
  /** Whether more conversations exist on the server (keyset pagination). */
  hasMoreConversations?: boolean;
  /** Load the next page of conversations from the server. */
  onLoadMoreConversations?: () => void;
  /** Which edge the desktop sidebar docks to (persisted by the caller). */
  dock?: SidebarDock;
  /** Compact icon rail instead of the full column (persisted by the caller). */
  collapsed?: boolean;
  onToggleCollapsed?: () => void;
  onChangeDock?: (dock: SidebarDock) => void;
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
  onShareConversation,
  onRenameConversation,
  onPinConversation,
  onArchiveConversation,
  archivedConversations = [],
  showArchived = false,
  onToggleArchived,
  onRestoreConversation,
  archivedLoading = false,
  activeTab,
  setActiveTab,
  currentModel,
  onChangeModel,
  onSignOut,
  showAdmin = false,
  onOpenCommandPalette,
  hasMoreConversations = false,
  onLoadMoreConversations,
  dock = "left",
  collapsed = false,
  onToggleCollapsed,
  onChangeDock,
}: SidebarProps) => {
  const models = MODEL_CATALOG;

  const [chatFilter, setChatFilter] = React.useState("");
  const [chatSort, setChatSort] = React.useState<"newest" | "oldest" | "az">("newest");

  const filterAndSort = (items: ConversationItem[]) => {
    let list = items;
    if (chatFilter.trim()) {
      const q = chatFilter.trim().toLowerCase();
      list = list.filter((c) => c.title.toLowerCase().includes(q));
    }
    return [...list].sort((a, b) => {
      if (chatSort === "oldest") {
        return new Date(a.updated_at).getTime() - new Date(b.updated_at).getTime();
      }
      if (chatSort === "az") {
        return a.title.localeCompare(b.title);
      }
      return new Date(b.updated_at).getTime() - new Date(a.updated_at).getTime();
    });
  };

  const pinnedList = filterAndSort(conversations.filter((c) => c.is_pinned));
  const recentList = filterAndSort(conversations.filter((c) => !c.is_pinned));

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
    { tab: "projects", label: "Projects", Icon: FolderKanban },
    { tab: "usage", label: "Usage", Icon: BarChart2 },
    { tab: "settings", label: "Settings", Icon: Sliders },
    // Admin is only visible to admins; non-admins who somehow land here are
    // bounced back to chat by the parent page.
    ...(showAdmin ? [{ tab: "admin" as const, label: "Admin", Icon: Sparkles }] : []),
  ];

  const renderConversation = (c: ConversationItem) => (
    <div
      key={c.id}
      role="button"
      tabIndex={0}
      aria-label={`Open ${c.title}`}
      aria-current={activeConversationId === c.id ? "page" : undefined}
      onClick={() => onSelectConversation(c.id)}
      onKeyDown={(e) => {
        // Only activate for keys aimed at the row itself. Enter/Space on a
        // nested rename or action button belongs to that button.
        if (e.target !== e.currentTarget) return;
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          onSelectConversation(c.id);
        }
      }}
      className={`group flex items-center justify-between px-3 py-2 rounded-lg text-xs cursor-pointer transition-colors focus-visible:outline-2 focus-visible:outline-[var(--accent)] ${
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
              aria-label={`Save name for ${c.title}`}
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
              aria-label={`Cancel rename for ${c.title}`}
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
            className="opacity-0 group-hover:opacity-100 focus-visible:opacity-100 text-[var(--text-faint)] hover:text-[var(--accent)] p-0.5 transition-opacity focus-visible:outline-2 focus-visible:outline-[var(--accent)]"
          >
            <Pencil className="w-3.5 h-3.5" />
          </button>
        )}
        {onPinConversation && (
          <button
            onClick={(e) => {
              e.stopPropagation();
              void onPinConversation(c.id, !c.is_pinned);
            }}
            title={c.is_pinned ? "Unpin conversation" : "Pin conversation"}
            aria-label={c.is_pinned ? `Unpin ${c.title}` : `Pin ${c.title}`}
            className={
              c.is_pinned
                ? "text-[var(--accent-ink)] p-0.5"
                : "opacity-0 group-hover:opacity-100 focus-visible:opacity-100 text-[var(--text-faint)] hover:text-[var(--accent-ink)] p-0.5 transition-opacity focus-visible:outline-2 focus-visible:outline-[var(--accent)]"
            }
          >
            <Pin className="w-3.5 h-3.5" />
          </button>
        )}
        {onForkConversation && (
          <button
            onClick={(e) => onForkConversation(c.id, e)}
            title="Fork/Branch conversation"
            aria-label={`Fork ${c.title}`}
            className="opacity-0 group-hover:opacity-100 focus-visible:opacity-100 text-[var(--text-faint)] hover:text-[var(--accent-ink)] p-0.5 transition-opacity focus-visible:outline-2 focus-visible:outline-[var(--accent)]"
          >
            <GitFork className="w-3.5 h-3.5" />
          </button>
        )}
        {onShareConversation && (
          <button
            onClick={(e) => onShareConversation(c.id, e)}
            title="Share conversation (read-only link)"
            aria-label={`Share ${c.title}`}
            className="opacity-0 group-hover:opacity-100 focus-visible:opacity-100 text-[var(--text-faint)] hover:text-[var(--accent-ink)] p-0.5 transition-opacity focus-visible:outline-2 focus-visible:outline-[var(--accent)]"
          >
            <Share2 className="w-3.5 h-3.5" />
          </button>
        )}
        {onArchiveConversation && (
          <button
            onClick={(e) => onArchiveConversation(c.id, e)}
            title="Archive conversation (hidden, not deleted)"
            aria-label={`Archive ${c.title}`}
            className="opacity-0 group-hover:opacity-100 focus-visible:opacity-100 text-[var(--text-faint)] hover:text-[var(--accent-ink)] p-0.5 transition-opacity focus-visible:outline-2 focus-visible:outline-[var(--accent)]"
          >
            <Archive className="w-3.5 h-3.5" />
          </button>
        )}
        <button
          onClick={(e) => onDeleteConversation(c.id, e)}
          title="Delete conversation"
          aria-label={`Delete ${c.title}`}
          className="opacity-0 group-hover:opacity-100 focus-visible:opacity-100 text-[var(--text-faint)] hover:text-[var(--status-danger)] p-0.5 transition-opacity focus-visible:outline-2 focus-visible:outline-[var(--accent)]"
        >
          <Trash2 className="w-3.5 h-3.5" />
        </button>
          </>
        )}
      </div>
    </div>
  );

  // A collapsed sidebar is an icon rail, not a hidden sidebar: the actions a
  // user reaches for without thinking (new chat, navigation, dock/expand) stay
  // visible and labelled, while the conversation list yields the space back to
  // the transcript.
  if (collapsed) {
    return (
      <aside
        className={`w-16 h-dvh flex flex-col items-center gap-2 py-3 bg-[var(--bg-main)] border-[var(--border-subtle)] select-none ${
          dock === "right" ? "border-l" : "border-r"
        }`}
      >
        <Image src="/nexus-logo.svg" alt="Nexus" width={28} height={28} className="w-7 h-7 rounded-lg" />
        <div className="flex flex-col items-center gap-1">
          {onChangeDock && (
            <button
              type="button"
              onClick={() => onChangeDock(dock === "left" ? "right" : "left")}
              title={dock === "left" ? "Dock sidebar on the right" : "Dock sidebar on the left"}
              aria-label={dock === "left" ? "Dock sidebar on the right" : "Dock sidebar on the left"}
              className="p-2 rounded-lg text-[var(--text-muted)] hover:text-[var(--text-primary)] hover:bg-[var(--bg-surface)] focus-visible:outline-2 focus-visible:outline-[var(--accent)]"
            >
              <PanelRight className="w-4 h-4" aria-hidden="true" />
            </button>
          )}
          {onToggleCollapsed && (
            <button
              type="button"
              onClick={onToggleCollapsed}
              title="Expand sidebar"
              aria-label="Expand sidebar"
              aria-pressed={true}
              className="p-2 rounded-lg text-[var(--text-muted)] hover:text-[var(--text-primary)] hover:bg-[var(--bg-surface)] focus-visible:outline-2 focus-visible:outline-[var(--accent)]"
            >
              <PanelLeftOpen className="w-4 h-4" aria-hidden="true" />
            </button>
          )}
        </div>
        <button
          type="button"
          onClick={onNewChat}
          title="New conversation"
          aria-label="New conversation"
          className="p-2 rounded-lg bg-[var(--accent)] text-[var(--accent-foreground)] hover:bg-[var(--accent-hover)] focus-visible:outline-2 focus-visible:outline-[var(--accent)]"
        >
          <Plus className="w-4 h-4" aria-hidden="true" />
        </button>
        <nav aria-label="Workspace" className="flex flex-col items-center gap-1 w-full px-2">
          {navItems.map(({ tab, label, Icon }) => (
            <button
              key={tab}
              type="button"
              onClick={() => setActiveTab(tab)}
              title={label}
              aria-label={`Go to ${label}`}
              aria-current={activeTab === tab ? "page" : undefined}
              className={`p-2 rounded-lg w-full flex items-center justify-center transition-colors focus-visible:outline-2 focus-visible:outline-[var(--accent)] ${
                activeTab === tab
                  ? "bg-[var(--accent-soft)] text-[var(--accent-ink)]"
                  : "text-[var(--text-muted)] hover:bg-[var(--bg-surface)] hover:text-[var(--text-primary)]"
              }`}
            >
              <Icon className="w-4 h-4" />
            </button>
          ))}
        </nav>
        {onSignOut && (
          <button
            type="button"
            onClick={onSignOut}
            title="Sign out"
            aria-label="Sign out"
            className="mt-auto p-2 rounded-lg text-[var(--text-faint)] hover:text-[var(--status-danger)] hover:bg-[var(--bg-surface)] focus-visible:outline-2 focus-visible:outline-[var(--accent)]"
          >
            <LogOut className="w-4 h-4" aria-hidden="true" />
          </button>
        )}
      </aside>
    );
  }

  return (
    <aside
      className={`w-72 h-dvh flex flex-col bg-[var(--bg-main)] border-[var(--border-subtle)] select-none ${
        dock === "right" ? "border-l" : "border-r"
      }`}
    >
      {/* Brand Header */}
      <div className="p-4 flex items-center justify-between border-b border-[var(--border-subtle)]">
        <div className="flex items-center gap-2.5">
          <Image src="/nexus-logo.svg" alt="Nexus" width={32} height={32} className="w-8 h-8 rounded-lg" />
          <div>
            <h1 className="font-semibold text-sm tracking-tight text-[var(--text-primary)]">
              Nexus AI
            </h1>
            <span className="text-[10px] text-[var(--text-faint)] font-medium tracking-wider uppercase">
              Agentic assistant
            </span>
          </div>
        </div>
        <div className="flex items-center gap-0.5">
          {(onChangeDock || onToggleCollapsed) && (
            <div className="flex items-center gap-0.5">
              {onChangeDock && (
                <button
                  type="button"
                  onClick={() => onChangeDock(dock === "left" ? "right" : "left")}
                  title={dock === "left" ? "Dock sidebar on the right" : "Dock sidebar on the left"}
                  aria-label={dock === "left" ? "Dock sidebar on the right" : "Dock sidebar on the left"}
                  className="p-1.5 rounded-lg text-[var(--text-faint)] hover:text-[var(--text-primary)] hover:bg-[var(--bg-surface)] transition-colors focus-visible:outline-2 focus-visible:outline-[var(--accent)]"
                >
                  <PanelRight className="w-4 h-4" aria-hidden="true" />
                </button>
              )}
              {onToggleCollapsed && (
                <button
                  type="button"
                  onClick={onToggleCollapsed}
                  title={collapsed ? "Expand sidebar" : "Collapse sidebar"}
                  aria-label={collapsed ? "Expand sidebar" : "Collapse sidebar"}
                  aria-pressed={collapsed}
                  className="p-1.5 rounded-lg text-[var(--text-faint)] hover:text-[var(--text-primary)] hover:bg-[var(--bg-surface)] transition-colors focus-visible:outline-2 focus-visible:outline-[var(--accent)]"
                >
                  {collapsed ? (
                    <PanelLeftOpen className="w-4 h-4" aria-hidden="true" />
                  ) : (
                    <PanelLeftClose className="w-4 h-4" aria-hidden="true" />
                  )}
                </button>
              )}
            </div>
          )}
          {onSignOut && (
            <button
              onClick={onSignOut}
              title="Sign out"
              aria-label="Sign out"
              className="p-1.5 rounded-lg text-[var(--text-faint)] hover:text-[var(--status-danger)] hover:bg-[var(--bg-surface)] transition-colors focus-visible:outline-2 focus-visible:outline-[var(--accent)]"
            >
              <LogOut className="w-4 h-4" />
            </button>
          )}
        </div>
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
              // The automatic tiers are shown so the picker tells the whole
              // truth about routing, but disabled: the backend only resolves
              // the two group ids, so a selectable entry would be a lie.
              <option key={m.id} value={m.id} disabled={!m.selectable}>
                {m.name} ({m.provider}){m.selectable ? "" : " — automatic"}
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

      {/* Conversation Filter & Sort */}
      {conversations.length > 2 && (
        <div className="px-3 pb-2 flex items-center gap-1.5">
          <div className="relative flex-1">
            <Search className="w-3 h-3 absolute left-2 top-2 text-[var(--text-faint)]" />
            <input
              type="text"
              value={chatFilter}
              onChange={(e) => setChatFilter(e.target.value)}
              placeholder="Filter chats…"
              className="w-full bg-[var(--bg-surface)] border border-[var(--border-subtle)] rounded-lg pl-6 pr-6 py-1 text-[11px] text-[var(--text-primary)] placeholder-[var(--text-faint)] focus:outline-none focus:border-[var(--accent)]"
            />
            {chatFilter && (
              <button
                onClick={() => setChatFilter("")}
                className="absolute right-2 top-1.5 text-[var(--text-faint)] hover:text-[var(--text-primary)]"
              >
                <X className="w-3 h-3" />
              </button>
            )}
          </div>
          <select
            value={chatSort}
            onChange={(e) => setChatSort(e.target.value as "newest" | "oldest" | "az")}
            className="bg-[var(--bg-surface)] border border-[var(--border-subtle)] rounded-lg px-1.5 py-1 text-[10px] text-[var(--text-secondary)] focus:outline-none"
            title="Sort conversations"
          >
            <option value="newest">Recent</option>
            <option value="oldest">Oldest</option>
            <option value="az">A-Z</option>
          </select>
        </div>
      )}

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
          {hasMoreConversations && onLoadMoreConversations && (
            <button
              onClick={onLoadMoreConversations}
              className="w-full mt-2 py-1.5 text-[11px] font-medium text-[var(--accent-ink)] hover:text-[var(--text-primary)] hover:bg-[var(--bg-surface)] rounded-md border border-dashed border-[var(--border-subtle)] transition-colors"
            >
              Show more conversations
            </button>
          )}
          {recentList.length === 0 && (
            <p className="text-[11px] text-[var(--text-faint)] px-2 py-3 text-center">
              No recent conversations
            </p>
          )}
        </div>

        {/* Archived conversations stay reachable without cluttering the list. */}
        {onToggleArchived && (
          <div className="pt-2 border-t border-[var(--border-subtle)]">
            <button
              type="button"
              onClick={onToggleArchived}
              aria-expanded={showArchived}
              className="w-full flex items-center gap-1.5 px-2 py-1.5 rounded-md text-[11px] font-medium text-[var(--text-faint)] hover:text-[var(--text-primary)] hover:bg-[var(--bg-surface)] transition-colors"
            >
              {showArchived ? (
                <ChevronDown className="w-3 h-3" />
              ) : (
                <ChevronRight className="w-3 h-3" />
              )}
              <Archive className="w-3 h-3" />
              Archived
              {archivedConversations.length > 0 && (
                <span className="ml-1 font-mono text-[10px]">
                  ({archivedConversations.length})
                </span>
              )}
            </button>

            {showArchived && (
              <div className="mt-1 space-y-1">
                {archivedLoading ? (
                  <p className="text-[11px] text-[var(--text-faint)] px-2 py-2">
                    Loading…
                  </p>
                ) : archivedConversations.length === 0 ? (
                  <p className="text-[11px] text-[var(--text-faint)] px-2 py-2">
                    Nothing archived.
                  </p>
                ) : (
                  archivedConversations.map((c) => (
                    <div
                      key={c.id}
                      role="button"
                      tabIndex={0}
                      aria-label={`Restore ${c.title}`}
                      onClick={() => onRestoreConversation?.(c.id)}
                      onKeyDown={(e) => {
                        if (e.target !== e.currentTarget) return;
                        if (e.key === "Enter" || e.key === " ") {
                          e.preventDefault();
                          onRestoreConversation?.(c.id);
                        }
                      }}
                      className="group flex items-center justify-between px-3 py-1.5 rounded-lg text-[11px] text-[var(--text-faint)] hover:bg-[var(--bg-surface)] cursor-pointer focus-visible:outline-2 focus-visible:outline-[var(--accent)]"
                    >
                      <span className="truncate">{c.title}</span>
                      {onRestoreConversation && (
                        <ArchiveRestore className="w-3.5 h-3.5 shrink-0" aria-hidden="true" />
                      )}
                    </div>
                  ))
                )}
              </div>
            )}
          </div>
        )}
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