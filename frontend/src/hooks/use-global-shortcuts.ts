"use client";

import { useEffect } from "react";

export interface GlobalShortcutsOptions {
  onToggleHelp: () => void;
  onCloseHelp: () => void;
  onToggleTheme: () => void;
  onStopGeneration: () => void;
  isGenerating: boolean;
  helpOpen: boolean;
  /** Any modal that owns Escape while it is open. */
  overlaysOpen: boolean;
}

function isTypingTarget(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false;
  return (
    target.isContentEditable ||
    target.tagName === "INPUT" ||
    target.tagName === "TEXTAREA" ||
    target.tagName === "SELECT"
  );
}

/**
 * Window-level shortcuts. Composer-local chords (`Cmd/Ctrl+Enter`) live in
 * ChatInput because the draft itself lives there; this hook owns only
 * app-wide bindings and deliberately yields Escape to whatever dialog is open.
 */
export function useGlobalShortcuts(options: GlobalShortcutsOptions) {
  const {
    onToggleHelp,
    onCloseHelp,
    onToggleTheme,
    onStopGeneration,
    isGenerating,
    helpOpen,
    overlaysOpen,
  } = options;

  useEffect(() => {
    const onKeyDown = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.shiftKey && e.key.toLowerCase() === "d") {
        e.preventDefault();
        onToggleTheme();
        return;
      }
      if (e.key === "?" && !isTypingTarget(e.target)) {
        e.preventDefault();
        onToggleHelp();
        return;
      }
      if (e.key === "Escape") {
        if (helpOpen) {
          onCloseHelp();
        } else if (!overlaysOpen && isGenerating) {
          e.preventDefault();
          onStopGeneration();
        }
      }
    };

    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [
    onToggleHelp,
    onCloseHelp,
    onToggleTheme,
    onStopGeneration,
    isGenerating,
    helpOpen,
    overlaysOpen,
  ]);
}
