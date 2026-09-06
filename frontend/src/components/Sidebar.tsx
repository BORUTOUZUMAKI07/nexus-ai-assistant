"use client";

import React from "react";
import {
  MessageSquare,
  Plus,
  Compass,
  FileText,
  Sliders,
  BarChart2,
  Trash2,
  Cpu,
  Pin,
  Sparkles,
  LogOut,
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
  activeTab: "chat" | "files" | "settings" | "usage" | "admin";
  setActiveTab: (tab: "chat" | "files" | "settings" | "usage" | "admin") => void;
  currentModel: string;
  onChangeModel: (model: string) => void;
  onSignOut?: () => void;
}

export const Sidebar: React.FC<SidebarProps> = ({
  conversations,
  activeConversationId,
  onSelectConversation,
  onNewChat,
  onDeleteConversation,
  activeTab,
  setActiveTab,
  currentModel,
  onChangeModel,
  onSignOut,
}) => {
  const models = [
    { id: "llama-3.3-70b-versatile", name: "Llama 3.3 70B", provider: "Groq (Fast)" },
    { id: "deepseek-r1-distill-llama-70b", name: "DeepSeek R1", provider: "Reasoning" },
    { id: "llama-3.1-8b-instant", name: "Llama 3.1 8B", provider: "Groq (Instant)" },
  ];

  const pinnedList = conversations.filter((c) => c.is_pinned);
  const recentList = conversations.filter((c) => !c.is_pinned);

  return (
    <aside className="w-72 h-screen flex flex-col bg-[#0b0e17] border-r border-[var(--border-subtle)] select-none">
      {/* Brand Header */}
      <div className="p-4 flex items-center justify-between border-b border-[var(--border-subtle)]">
        <div className="flex items-center gap-2.5">
          <div className="w-8 h-8 rounded-lg bg-gradient-to-tr from-violet-600 via-indigo-600 to-cyan-400 flex items-center justify-center shadow-lg shadow-violet-500/20">
            <Sparkles className="w-4 h-4 text-white" />
          </div>
          <div>
            <h1 className="font-semibold text-sm tracking-wide text-white">NEXUS AI</h1>
            <span className="text-[10px] text-cyan-400 font-medium tracking-wider uppercase">
              Production Assistant
            </span>
          </div>
        </div>
        {onSignOut && (
          <button
            onClick={onSignOut}
            title="Sign out"
            className="p-1.5 rounded-lg text-neutral-400 hover:text-rose-400 hover:bg-[var(--bg-surface)] transition-colors"
          >
            <LogOut className="w-4 h-4" />
          </button>
        )}
      </div>

      {/* Model Selector Card */}
      <div className="px-3 pt-3">
        <div className="p-2.5 rounded-xl bg-[var(--bg-surface)] border border-[var(--border-subtle)]">
          <div className="flex items-center justify-between mb-1.5 px-1">
            <span className="text-[11px] text-neutral-400 font-medium flex items-center gap-1.5">
              <Cpu className="w-3 h-3 text-violet-400" /> Active Model
            </span>
            <span className="text-[9px] px-1.5 py-0.5 rounded-full bg-emerald-500/10 text-emerald-400 font-mono">
              100% Free Tier
            </span>
          </div>
          <select
            value={currentModel}
            onChange={(e) => onChangeModel(e.target.value)}
            className="w-full bg-[#080a11] text-xs text-neutral-200 border border-[var(--border-subtle)] rounded-lg px-2.5 py-1.5 outline-none focus:border-violet-500 transition-colors"
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
          className="w-full flex items-center justify-center gap-2 py-2.5 px-4 rounded-xl bg-gradient-to-r from-violet-600 to-indigo-600 hover:from-violet-500 hover:to-indigo-500 text-white text-xs font-medium shadow-md shadow-violet-600/25 transition-all active:scale-[0.98]"
        >
          <Plus className="w-4 h-4" />
          <span>New Conversation</span>
        </button>
      </div>

      {/* Conversation Thread History */}
      <div className="flex-1 overflow-y-auto px-3 py-2 space-y-4">
        {pinnedList.length > 0 && (
          <div>
            <div className="text-[11px] font-medium text-neutral-400 px-2 mb-1.5 flex items-center gap-1">
              <Pin className="w-3 h-3 text-violet-400" /> Pinned
            </div>
            <div className="space-y-1">
              {pinnedList.map((c) => (
                <div
                  key={c.id}
                  onClick={() => onSelectConversation(c.id)}
                  className={`group flex items-center justify-between px-3 py-2 rounded-lg text-xs cursor-pointer transition-colors ${
                    activeConversationId === c.id
                      ? "bg-violet-600/15 text-violet-200 border border-violet-500/30"
                      : "text-neutral-300 hover:bg-[var(--bg-surface)] hover:text-white"
                  }`}
                >
                  <div className="flex items-center gap-2 truncate">
                    <MessageSquare className="w-3.5 h-3.5 shrink-0 opacity-70" />
                    <span className="truncate">{c.title}</span>
                  </div>
                  <button
                    onClick={(e) => onDeleteConversation(c.id, e)}
                    className="opacity-0 group-hover:opacity-100 text-neutral-400 hover:text-rose-400 p-0.5 transition-opacity"
                  >
                    <Trash2 className="w-3.5 h-3.5" />
                  </button>
                </div>
              ))}
            </div>
          </div>
        )}

        <div>
          <div className="text-[11px] font-medium text-neutral-400 px-2 mb-1.5">Recent Conversations</div>
          <div className="space-y-1">
            {recentList.map((c) => (
              <div
                key={c.id}
                onClick={() => onSelectConversation(c.id)}
                className={`group flex items-center justify-between px-3 py-2 rounded-lg text-xs cursor-pointer transition-colors ${
                  activeConversationId === c.id
                    ? "bg-violet-600/15 text-violet-200 border border-violet-500/30"
                    : "text-neutral-300 hover:bg-[var(--bg-surface)] hover:text-white"
                }`}
              >
                <div className="flex items-center gap-2 truncate">
                  <MessageSquare className="w-3.5 h-3.5 shrink-0 opacity-70" />
                  <span className="truncate">{c.title}</span>
                </div>
                <button
                  onClick={(e) => onDeleteConversation(c.id, e)}
                  className="opacity-0 group-hover:opacity-100 text-neutral-400 hover:text-rose-400 p-0.5 transition-opacity"
                >
                  <Trash2 className="w-3.5 h-3.5" />
                </button>
              </div>
            ))}
            {recentList.length === 0 && (
              <p className="text-[11px] text-neutral-400 px-2 py-4 italic text-center">No recent chats</p>
            )}
          </div>
        </div>
      </div>

      {/* Bottom Navigation Links */}
      <div className="p-3 border-t border-[var(--border-subtle)] space-y-1 bg-[#090b12]">
        <button
          onClick={() => setActiveTab("files")}
          className={`w-full flex items-center gap-2.5 px-3 py-2 rounded-lg text-xs transition-colors ${
            activeTab === "files"
              ? "bg-cyan-500/15 text-cyan-200 border border-cyan-500/30"
              : "text-neutral-400 hover:bg-[var(--bg-surface)] hover:text-white"
          }`}
        >
          <FileText className="w-4 h-4 text-cyan-400" />
          <span>Knowledge & RAG</span>
        </button>

        <button
          onClick={() => setActiveTab("usage")}
          className={`w-full flex items-center gap-2.5 px-3 py-2 rounded-lg text-xs transition-colors ${
            activeTab === "usage"
              ? "bg-emerald-500/15 text-emerald-200 border border-emerald-500/30"
              : "text-neutral-400 hover:bg-[var(--bg-surface)] hover:text-white"
          }`}
        >
          <BarChart2 className="w-4 h-4 text-emerald-400" />
          <span>Usage & Free Tier Budget</span>
        </button>

        <button
          onClick={() => setActiveTab("settings")}
          className={`w-full flex items-center gap-2.5 px-3 py-2 rounded-lg text-xs transition-colors ${
            activeTab === "settings"
              ? "bg-violet-500/15 text-violet-200 border border-violet-500/30"
              : "text-neutral-400 hover:bg-[var(--bg-surface)] hover:text-white"
          }`}
        >
          <Sliders className="w-4 h-4 text-violet-400" />
          <span>Settings & BYOK Keys</span>
        </button>

        <button
          onClick={() => setActiveTab("admin")}
          className={`w-full flex items-center gap-2.5 px-3 py-2 rounded-lg text-xs transition-colors ${
            activeTab === "admin"
              ? "bg-amber-500/15 text-amber-200 border border-amber-500/30"
              : "text-neutral-400 hover:bg-[var(--bg-surface)] hover:text-white"
          }`}
        >
          <Sparkles className="w-4 h-4 text-amber-400" />
          <span>Admin Center</span>
        </button>
      </div>
    </aside>
  );
};
