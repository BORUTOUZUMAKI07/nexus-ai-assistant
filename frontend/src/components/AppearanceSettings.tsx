"use client";

import React from "react";
import { Check } from "lucide-react";
import { ACCENTS, THEME_MODES, useTheme, type ThemeMode } from "@/lib/theme";

const LABELS: Record<ThemeMode, { title: string; hint: string }> = {
  light: { title: "Light", hint: "Paper surfaces, ink text" },
  dark: { title: "Dark", hint: "Void surfaces, paper text" },
  system: { title: "System", hint: "Follows your OS setting" },
};

type Palette = { bg: string; side: string; line: string; border: string; accent: string };

// Fixed colors on purpose — each tile must show its *target* theme, not the active one.
const PALETTES: Record<"light" | "dark", Palette> = {
  light: { bg: "#fafaf9", side: "#ffffff", line: "#d3d3cf", border: "#e7e7e4", accent: "#d7e600" },
  dark: { bg: "#08090a", side: "#0f1011", line: "#383b3f", border: "#23252a", accent: "#e4f222" },
};

/** Miniature app window in one palette. Module-level (not nested in Preview) so
 *  React doesn't recreate the component type on every render. */
function MiniWindow({ c }: { c: Palette }) {
  return (
    <div className="flex h-full w-full" style={{ background: c.bg }}>
      <div className="h-full w-[28%]" style={{ background: c.side, borderRight: `1px solid ${c.border}` }}>
        <div className="mx-1.5 mt-2 h-1 rounded-full" style={{ background: c.accent }} />
        <div className="mx-1.5 mt-1.5 h-1 rounded-full" style={{ background: c.line }} />
        <div className="mx-1.5 mt-1 h-1 w-3/4 rounded-full" style={{ background: c.line }} />
      </div>
      <div className="flex-1 p-2">
        <div className="h-1.5 w-2/3 rounded-full" style={{ background: c.line }} />
        <div className="mt-1.5 h-1.5 w-1/2 rounded-full" style={{ background: c.line }} />
        <div className="mt-3 ml-auto h-2.5 w-1/3 rounded-full" style={{ background: c.accent }} />
      </div>
    </div>
  );
}

function Preview({ mode }: { mode: ThemeMode }) {
  return (
    <div className="relative h-16 w-full overflow-hidden rounded-md border border-[var(--border-subtle)]" aria-hidden="true">
      {mode === "system" ? (
        <>
          <div className="absolute inset-0"><MiniWindow c={PALETTES.light} /></div>
          <div className="absolute inset-0" style={{ clipPath: "polygon(100% 0, 100% 100%, 0 100%)" }}>
            <MiniWindow c={PALETTES.dark} />
          </div>
        </>
      ) : (
        <MiniWindow c={PALETTES[mode]} />
      )}
    </div>
  );
}

export const AppearanceSettings: React.FC = () => {
  const { mode, accent, setMode, setAccent } = useTheme();

  return (
    <div className="glass-panel p-6 space-y-5">
      <div className="flex items-baseline justify-between border-b border-[var(--border-subtle)] pb-2">
        <h3 className="text-sm font-medium text-[var(--text-primary)]">Appearance</h3>
        <span className="text-[11px] text-[var(--text-faint)]">Applies instantly on this device</span>
      </div>

      <fieldset>
        <legend className="mb-2 text-[11px] font-medium text-[var(--text-secondary)]">Theme</legend>
        <div className="grid grid-cols-3 gap-3">
          {THEME_MODES.map((m) => (
            <label key={m} className="group cursor-pointer">
              <input
                type="radio"
                name="theme-mode"
                value={m}
                checked={mode === m}
                onChange={() => setMode(m)}
                className="peer sr-only"
              />
              <div className="rounded-lg border border-[var(--border-subtle)] p-2 transition-colors group-hover:border-[var(--border-strong)] peer-checked:border-[var(--border-active)] peer-checked:bg-[var(--accent-soft)] peer-focus-visible:outline peer-focus-visible:outline-2 peer-focus-visible:outline-offset-2 peer-focus-visible:outline-[var(--accent-ink)]">
                <Preview mode={m} />
                <div className="mt-2 flex items-center justify-between px-0.5">
                  <span className="text-xs font-medium text-[var(--text-primary)]">{LABELS[m].title}</span>
                  {mode === m && <Check className="h-3.5 w-3.5 text-[var(--accent-ink)]" aria-hidden="true" />}
                </div>
                <p className="px-0.5 text-[10px] text-[var(--text-muted)]">{LABELS[m].hint}</p>
              </div>
            </label>
          ))}
        </div>
      </fieldset>

      <fieldset>
        <legend className="mb-2 text-[11px] font-medium text-[var(--text-secondary)]">Accent</legend>
        <div className="flex flex-wrap gap-2">
          {ACCENTS.map((a) => (
            <label key={a.id} className="cursor-pointer">
              <input
                type="radio"
                name="accent"
                value={a.id}
                checked={accent === a.id}
                onChange={() => setAccent(a.id)}
                className="peer sr-only"
              />
              <span className="flex items-center gap-2 rounded-full border border-[var(--border-subtle)] px-3 py-1.5 text-xs text-[var(--text-secondary)] transition-colors hover:border-[var(--border-strong)] peer-checked:border-[var(--border-active)] peer-checked:bg-[var(--accent-soft)] peer-checked:text-[var(--text-primary)] peer-focus-visible:outline peer-focus-visible:outline-2 peer-focus-visible:outline-offset-2 peer-focus-visible:outline-[var(--accent-ink)]">
                <span
                  className="h-3 w-3 rounded-full border border-black/10"
                  style={{ background: a.swatch }}
                  aria-hidden="true"
                />
                {a.label}
              </span>
            </label>
          ))}
        </div>
      </fieldset>
    </div>
  );
};
