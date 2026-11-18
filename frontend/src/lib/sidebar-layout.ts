"use client";

import * as React from "react";

/**
 * Sidebar layout preference (dock side + collapsed rail).
 *
 * Deliberately local: `UserSettings.custom_settings` is a free-form JSON blob
 * with no UI prefs contract, and the backend `theme` column cannot tell
 * "never chose" from "chose dark" (see lib/theme.ts). Writing a new hosted
 * settings field would be a backend change this slice must not make, so the
 * preference is stored per signed-in user in localStorage. Anonymous sessions
 * get their own bucket and inherit the preference when they sign in as that
 * user.
 */
export type SidebarDock = "left" | "right";

export interface SidebarLayout {
  dock: SidebarDock;
  collapsed: boolean;
}

export const DEFAULT_SIDEBAR_LAYOUT: SidebarLayout = {
  dock: "left",
  collapsed: false,
};

const DEFAULT_SNAPSHOT = JSON.stringify(DEFAULT_SIDEBAR_LAYOUT);
const listeners = new Set<() => void>();

function emit() {
  for (const listener of listeners) listener();
}

// A storage event covers changes made in another tab; `emit` covers the write
// made by this hook, which browsers do not echo back to the same document.
function subscribe(listener: () => void) {
  listeners.add(listener);
  if (typeof window !== "undefined") window.addEventListener("storage", listener);
  return () => {
    listeners.delete(listener);
    if (typeof window !== "undefined") window.removeEventListener("storage", listener);
  };
}

export const sidebarLayoutKey = (userId: string | null) =>
  `nexus-sidebar-layout:${userId ?? "anonymous"}`;

function readStored(userId: string | null): SidebarLayout {
  try {
    const raw = window.localStorage.getItem(sidebarLayoutKey(userId));
    if (!raw) return DEFAULT_SIDEBAR_LAYOUT;
    const parsed = JSON.parse(raw) as Partial<SidebarLayout>;
    return {
      dock: parsed.dock === "right" ? "right" : "left",
      collapsed: parsed.collapsed === true,
    };
  } catch {
    // Blocked storage or a value written by an older build: fall back to the
    // safe default rather than taking the shell down with it.
    return DEFAULT_SIDEBAR_LAYOUT;
  }
}

/**
 * Reads the preference as an external store instead of mirroring it into
 * React state in an effect. That keeps the server snapshot deterministic (the
 * default), avoids a hydration-time localStorage read, and still lets a write
 * in this tab re-render every consumer immediately.
 */
export function useSidebarLayout(userId: string | null) {
  const getSnapshot = React.useCallback(
    () => (typeof window === "undefined" ? DEFAULT_SNAPSHOT : JSON.stringify(readStored(userId))),
    [userId],
  );
  const snapshot = React.useSyncExternalStore(subscribe, getSnapshot, () => DEFAULT_SNAPSHOT);
  const layout = React.useMemo(() => JSON.parse(snapshot) as SidebarLayout, [snapshot]);

  const update = React.useCallback(
    (patch: Partial<SidebarLayout>) => {
      const next = { ...layout, ...patch };
      try {
        window.localStorage.setItem(sidebarLayoutKey(userId), JSON.stringify(next));
      } catch {
        // Private mode / blocked storage: the change still applies this session.
      }
      emit();
    },
    [layout, userId],
  );

  return {
    layout,
    setDock: (dock: SidebarDock) => update({ dock }),
    toggleCollapsed: () => update({ collapsed: !layout.collapsed }),
  };
}
