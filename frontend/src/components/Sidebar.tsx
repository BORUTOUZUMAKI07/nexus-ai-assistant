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
} from "lucide-react";

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
  activeTab: "chat" | "files" | "settings" | "usage" | "admin";
  setActiveTab: (tab: "chat" | "files" | "settings" | "usage" | "admin") => void;
  currentModel: string;
  onChangeModel: (model: string) => void;
  onSignOut?: () => void;
  /** When false (the default) the Admin nav item is hidden entirely. */
  showAdmin?: boolean;
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
  activeTab,
  setActiveTab,
  currentModel,
  onChangeModel,
  onSignOut,
  showAdmin = false,
}: SidebarProps) => {
  const models = [
    { id: "llama-3.3-70b-versatile", name: "Llama 3.3 70B", provider: "Groq (Fast)" },
    { id: "deepseek-r1-distill-llama-70b", name: "DeepSeek R1", provider: "Reasoning" },
    { id: "llama-3.1-8b-instant", name: "Llama 3.1 8B", provider: "Groq (Instant)" },
  ];

  const pinnedList = conversations.filter((c) => c.is_pinned);
  const recentList = conversations.filter((c) => !c.is_pinned);

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
          ? "bg-[var(--accent-soft)] text-[var(--accent-hover)] border border-[var(--accent)]"
          : "text-[var(--text-muted)] hover:bg-[var(--bg-surface)] hover:text-white border border-transparent"
      }`}
    >
      <div className="flex items-center gap-2 truncate">
        <MessageSquare className="w-3.5 h-3.5 shrink-0 opacity-70" />
        <span className="truncate">{c.title}</span>
      </div>
      <div className="flex items-center gap-1">
        {onForkConversation && (
          <button
            onClick={(e) => onForkConversation(c.id, e)}
            title="Fork/Branch conversation"
            className="opacity-0 group-hover:opacity-100 text-[var(--text-faint)] hover:text-[var(--accent)] p-0.5 transition-opacity"
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
      </div>
    </div>
  );

  return (
    <aside className="w-72 h-screen flex flex-col bg-[var(--bg-main)] border-r border-[var(--border-subtle)] select-none">
      {/* Brand Header */}
      <div className="p-4 flex items-center justify-between border-b border-[var(--border-subtle)]">
        <div className="flex items-center gap-2.5">
          <div className="w-8 h-8 rounded-lg bg-[var(--bg-surface-elevated)] border border-[var(--border-subtle)] flex items-center justify-center">
            <Sparkles className="w-4 h-4 text-[var(--accent)]" />
          </div>
          <div>
            <h1 className="font-semibold text-sm tracking-tight text-white">
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

      {/* Model Selector */}
      <div className="px-3 pt-3">
        <div className="p-2.5 rounded-xl border border-[var(--border-subtle)] bg-[var(--bg-surface)]">
          <div className="flex items-center justify-between mb-1.5 px-1">
            <span className="text-[11px] text-[var(--text-muted)] font-medium flex items-center gap-1.5">
              <Cpu className="w-3 h-3 text-[var(--accent)]" /> Model
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
                ? "bg-[var(--accent-soft)] text-[var(--accent-hover)] border border-[var(--accent)]"
                : "text-[var(--text-muted)] hover:bg-[var(--bg-surface)] hover:text-white border border-transparent"
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