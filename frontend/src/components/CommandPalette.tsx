"use client";

import React, { useEffect, useMemo } from "react";
import {
  MessageSquare,
  Plus,
  FileText,
  Sliders,
  BarChart2,
  Shield,
  Cpu,
  Sun,
  Moon,
  Monitor,
  LogOut,
  Palette,
  Pin,
} from "lucide-react";
import {
  CommandDialog,
  CommandInput,
  CommandList,
  CommandEmpty,
  CommandGroup,
  CommandItem,
  CommandShortcut,
} from "@/components/ui/command";
import { MODELS } from "@/lib/models";
import { ACCENTS, useTheme } from "@/lib/theme";
import type { ConversationItem } from "@/components/Sidebar";

export type AppTab = "chat" | "files" | "settings" | "usage" | "admin";

interface CommandPaletteProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  conversations: ConversationItem[];
  onSelectConversation: (id: string) => void;
  onNewChat: () => void;
  setActiveTab: (tab: AppTab) => void;
  onChangeModel: (model: string) => void;
  currentModel: string;
  onSignOut?: () => void;
  showAdmin?: boolean;
}

/**
 * ⌘K / Ctrl+K palette. Every entry maps onto an action the app already
 * exposes (tabs, conversations, model, theme) — nothing is palette-only.
 */
export const CommandPalette: React.FC<CommandPaletteProps> = ({
  open,
  onOpenChange,
  conversations,
  onSelectConversation,
  onNewChat,
  setActiveTab,
  onChangeModel,
  currentModel,
  onSignOut,
  showAdmin = false,
}) => {
  const { setMode, setAccent } = useTheme();

  // Global shortcut. Toggles, so the same chord closes it.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        onOpenChange(!open);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onOpenChange]);

  const run = (fn: () => void) => () => {
    onOpenChange(false);
    fn();
  };

  const recent = useMemo(() => conversations.slice(0, 12), [conversations]);

  const nav: { tab: AppTab; label: string; Icon: React.ElementType }[] = [
    { tab: "chat", label: "Go to Chat", Icon: MessageSquare },
    { tab: "files", label: "Go to Knowledge", Icon: FileText },
    { tab: "usage", label: "Go to Usage", Icon: BarChart2 },
    { tab: "settings", label: "Go to Settings", Icon: Sliders },
    ...(showAdmin ? [{ tab: "admin" as AppTab, label: "Go to Admin", Icon: Shield }] : []),
  ];

  return (
    <CommandDialog open={open} onOpenChange={onOpenChange}>
      <CommandInput placeholder="Search conversations, or type a command…" />
      <CommandList>
        <CommandEmpty>No results. Try a conversation title or “theme”.</CommandEmpty>

        <CommandGroup heading="Actions">
          <CommandItem value="new chat conversation" onSelect={run(onNewChat)}>
            <Plus className="h-4 w-4" aria-hidden="true" /> New chat
          </CommandItem>
        </CommandGroup>

        <CommandGroup heading="Navigate">
          {nav.map(({ tab, label, Icon }) => (
            <CommandItem key={tab} value={label} onSelect={run(() => setActiveTab(tab))}>
              <Icon className="h-4 w-4" aria-hidden="true" /> {label}
            </CommandItem>
          ))}
        </CommandGroup>

        {recent.length > 0 && (
          <CommandGroup heading="Conversations">
            {recent.map((c) => (
              <CommandItem
                key={c.id}
                value={`conversation ${c.title} ${c.id}`}
                onSelect={run(() => onSelectConversation(c.id))}
              >
                {c.is_pinned ? (
                  <Pin className="h-4 w-4" aria-hidden="true" />
                ) : (
                  <MessageSquare className="h-4 w-4" aria-hidden="true" />
                )}
                <span className="truncate">{c.title || "Untitled conversation"}</span>
              </CommandItem>
            ))}
          </CommandGroup>
        )}

        <CommandGroup heading="Model">
          {MODELS.map((m) => (
            <CommandItem
              key={m.id}
              value={`model ${m.name} ${m.provider}`}
              onSelect={run(() => onChangeModel(m.id))}
            >
              <Cpu className="h-4 w-4" aria-hidden="true" />
              {m.name}
              <CommandShortcut>{currentModel === m.id ? "current" : m.provider}</CommandShortcut>
            </CommandItem>
          ))}
        </CommandGroup>

        <CommandGroup heading="Appearance">
          <CommandItem value="theme light mode" onSelect={run(() => setMode("light"))}>
            <Sun className="h-4 w-4" aria-hidden="true" /> Light theme
          </CommandItem>
          <CommandItem value="theme dark mode" onSelect={run(() => setMode("dark"))}>
            <Moon className="h-4 w-4" aria-hidden="true" /> Dark theme
          </CommandItem>
          <CommandItem value="theme system mode auto" onSelect={run(() => setMode("system"))}>
            <Monitor className="h-4 w-4" aria-hidden="true" /> Match system theme
          </CommandItem>
          {ACCENTS.map((a) => (
            <CommandItem
              key={a.id}
              value={`accent color ${a.label}`}
              onSelect={run(() => setAccent(a.id))}
            >
              <Palette className="h-4 w-4" aria-hidden="true" />
              Accent: {a.label}
              <span
                className="ml-auto h-3 w-3 rounded-full border border-[var(--border-strong)]"
                style={{ background: a.swatch }}
                aria-hidden="true"
              />
            </CommandItem>
          ))}
        </CommandGroup>

        {onSignOut && (
          <CommandGroup heading="Account">
            <CommandItem value="sign out log out" onSelect={run(onSignOut)}>
              <LogOut className="h-4 w-4" aria-hidden="true" /> Sign out
            </CommandItem>
          </CommandGroup>
        )}
      </CommandList>
    </CommandDialog>
  );
};
