"use client";

import { Toaster } from "sonner";
import { useTheme } from "@/lib/theme";

/** Toasts styled with the brand tokens so they follow light/dark automatically. */
export function AppToaster() {
  const { resolved } = useTheme();
  return (
    <Toaster
      theme={resolved}
      position="bottom-right"
      toastOptions={{
        style: {
          background: "var(--bg-surface-elevated)",
          color: "var(--text-primary)",
          border: "1px solid var(--border-subtle)",
          borderRadius: "10px",
          fontSize: "13px",
        },
      }}
    />
  );
}
