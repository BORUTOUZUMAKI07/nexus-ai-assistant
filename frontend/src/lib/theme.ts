"use client";

import { useSyncExternalStore } from "react";

/**
 * Client-side theme preference. Deliberately NOT read from UserSettings.theme:
 * the backend defaults that column to "dark" for every account, so it can't
 * distinguish "never chose" from "chose dark". Persisting locally keeps the
 * light default intact with zero backend changes.
 *
 * <html data-theme="light|dark">
 *
 * **There is exactly ONE brand accent — Electric Acid Lime — and it is not a
 * preference.** The old cyan/violet/emerald `data-accent` swaps were removed on
 * 2026-10-14 at the product owner's request ("only acid lime is good"). No
 * `data-accent` attribute is ever written; `THEME_INIT_SCRIPT` also clears any
 * stale value a returning visitor still has in storage from the previous build.
 */
export type ThemeMode = "light" | "dark" | "system";

export const THEME_KEY = "nexus-theme";

export const THEME_MODES: ThemeMode[] = ["light", "dark", "system"];

export interface ThemeState {
  mode: ThemeMode;
}

/** Runs before first paint (inlined in <head>) so there is no theme flash. */
export const THEME_INIT_SCRIPT = `(function(){try{var d=document.documentElement;var m=localStorage.getItem("${THEME_KEY}")||"light";var t=m==="system"?(window.matchMedia&&window.matchMedia("(prefers-color-scheme: dark)").matches?"dark":"light"):m;d.setAttribute("data-theme",t==="dark"?"dark":"light");d.removeAttribute("data-accent");}catch(e){}})();`;

const SERVER_STATE: ThemeState = { mode: "light" };
let cached: ThemeState = SERVER_STATE;
let initialised = false;
const listeners = new Set<() => void>();

function isMode(v: unknown): v is ThemeMode {
  return v === "light" || v === "dark" || v === "system";
}

function readStored(): ThemeState {
  try {
    const m = window.localStorage.getItem(THEME_KEY);
    return { mode: isMode(m) ? m : "light" };
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
  // Single accent: never write data-accent, and strip any value left over from
  // the previous multi-accent build so its swap block can't linger.
  root.removeAttribute("data-accent");
}

function commit(next: ThemeState) {
  if (next.mode === cached.mode) return;
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

function subscribe(listener: () => void) {
  if (!initialised) {
    initialised = true;
    cached = readStored();
  }
  listeners.add(listener);

  const onStorage = (e: StorageEvent) => {
    if (e.key === THEME_KEY || e.key === null) commit(readStored());
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
  };
}
