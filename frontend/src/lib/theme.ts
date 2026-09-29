"use client";

import { useSyncExternalStore } from "react";

/**
 * Client-side theme preference. Deliberately NOT read from UserSettings.theme:
 * the backend defaults that column to "dark" for every account, so it can't
 * distinguish "never chose" from "chose dark". Persisting locally keeps the
 * light default intact with zero backend changes.
 *
 * <html data-theme="light|dark"  data-accent="cyan|violet|emerald">
 * (lime is the unset default, so no data-accent attribute is written for it)
 */
export type ThemeMode = "light" | "dark" | "system";
export type Accent = "lime" | "cyan" | "violet" | "emerald";

export const THEME_KEY = "nexus-theme";
export const ACCENT_KEY = "nexus-accent";

export const THEME_MODES: ThemeMode[] = ["light", "dark", "system"];
export const ACCENTS: { id: Accent; label: string; swatch: string }[] = [
  { id: "lime", label: "Acid Lime", swatch: "#d7e600" },
  { id: "cyan", label: "Signal Teal", swatch: "#02b8cc" },
  { id: "violet", label: "Iris Violet", swatch: "#6366f1" },
  { id: "emerald", label: "Emerald", swatch: "#10b981" },
];

export interface ThemeState {
  mode: ThemeMode;
  accent: Accent;
}

/** Runs before first paint (inlined in <head>) so there is no theme flash. */
export const THEME_INIT_SCRIPT = `(function(){try{var d=document.documentElement;var m=localStorage.getItem("${THEME_KEY}")||"light";var a=localStorage.getItem("${ACCENT_KEY}")||"lime";var t=m==="system"?(window.matchMedia&&window.matchMedia("(prefers-color-scheme: dark)").matches?"dark":"light"):m;d.setAttribute("data-theme",t==="dark"?"dark":"light");if(a!=="lime"){d.setAttribute("data-accent",a);}}catch(e){}})();`;

const SERVER_STATE: ThemeState = { mode: "light", accent: "lime" };
let cached: ThemeState = SERVER_STATE;
let initialised = false;
const listeners = new Set<() => void>();

function isMode(v: unknown): v is ThemeMode {
  return v === "light" || v === "dark" || v === "system";
}
function isAccent(v: unknown): v is Accent {
  return v === "lime" || v === "cyan" || v === "violet" || v === "emerald";
}

function readStored(): ThemeState {
  try {
    const m = window.localStorage.getItem(THEME_KEY);
    const a = window.localStorage.getItem(ACCENT_KEY);
    return {
      mode: isMode(m) ? m : "light",
      accent: isAccent(a) ? a : "lime",
    };
  } catch {
    return SERVER_STATE;
  }
}

export function resolveMode(mode: ThemeMode): "light" | "dark" {
  if (mode !== "system") return mode;
  if (typeof window !== "undefined" && typeof window.matchMedia === "function") {
    return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
  }
  return "light";
}

function applyToDocument(state: ThemeState) {
  const root = document.documentElement;
  root.setAttribute("data-theme", resolveMode(state.mode));
  if (state.accent === "lime") root.removeAttribute("data-accent");
  else root.setAttribute("data-accent", state.accent);
}

function commit(next: ThemeState) {
  if (next.mode === cached.mode && next.accent === cached.accent) return;
  cached = next;
  applyToDocument(cached);
  listeners.forEach((l) => l());
}

export function setThemeMode(mode: ThemeMode) {
  try {
    window.localStorage.setItem(THEME_KEY, mode);
  } catch {
    /* storage blocked — still apply for this session */
  }
  commit({ ...cached, mode });
}

export function setAccent(accent: Accent) {
  try {
    window.localStorage.setItem(ACCENT_KEY, accent);
  } catch {
    /* storage blocked — still apply for this session */
  }
  commit({ ...cached, accent });
}

function subscribe(listener: () => void) {
  if (!initialised) {
    initialised = true;
    cached = readStored();
  }
  listeners.add(listener);

  const onStorage = (e: StorageEvent) => {
    if (e.key === THEME_KEY || e.key === ACCENT_KEY || e.key === null) commit(readStored());
  };
  window.addEventListener("storage", onStorage);

  // "system" follows the OS live
  const mq =
    typeof window.matchMedia === "function"
      ? window.matchMedia("(prefers-color-scheme: dark)")
      : null;
  const onSystem = () => {
    if (cached.mode === "system") applyToDocument(cached);
    listeners.forEach((l) => l());
  };
  mq?.addEventListener?.("change", onSystem);

  // Sync <html> with stored state now that we've read it
  applyToDocument(cached);

  return () => {
    listeners.delete(listener);
    window.removeEventListener("storage", onStorage);
    mq?.removeEventListener?.("change", onSystem);
  };
}

const getSnapshot = () => cached;
const getServerSnapshot = () => SERVER_STATE;

export function useTheme() {
  const state = useSyncExternalStore(subscribe, getSnapshot, getServerSnapshot);
  return {
    ...state,
    resolved: resolveMode(state.mode),
    setMode: setThemeMode,
    setAccent,
  };
}
