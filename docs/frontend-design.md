# Frontend Design System — "Refero" (Paper + Void)

This is the **contract** for every pixel the Nexus frontend renders. Future
generation (human or AI) must match this spec — the old "black-only / Void
default" design was deliberately replaced and must **not** come back.

> Verified against source at `frontend/src/app/globals.css`,
> `frontend/src/lib/theme.ts`, `frontend/src/app/layout.tsx`, commit `2a42a8c`.
> If this file contradicts the code, **trust the code** and update this file.

## 1. What the design is

**Two surfaces of one brand** (Linear & Cursor inspired, "Refero" spec):

- **Paper** — light theme, the **default**.
  `--bg-main: #fafaf9` (soft Paper floor), cards `#ffffff`, ink `#16171a`.
- **Void** — dark theme, preserved as a preference.
  `--bg-main: #08090a`, surfaces `#0f1011/#161718`, text white.

Both share the same scale: hairline borders, one **Electric Acid Lime** accent
(default) with **cyan / violet / emerald** accent swaps, soft rounded corners
(`--radius: 0.625rem`), Inter sans + Geist Mono.

There is **no third theme**. There is no "black glass only" variant. The app is
never dark-by-default for anonymous visitors.

## 2. How themes are applied (wiring)

- `<html data-theme="light|dark">` and `data-accent="cyan|violet|emerald"`.
  **Lime is the unset default — no `data-accent` attribute is written for it.**
  CSS scopes in `globals.css` key off these attributes:
  `:root` = Paper defaults; `[data-theme="dark"]` = Void overrides;
  `[data-accent="cyan|violet|emerald"]` layer accent swaps on top of either.
- `THEME_INIT_SCRIPT` (`src/lib/theme.ts`) is inlined in
  `src/app/layout.tsx` `<head>` BEFORE first paint → no light/dark flash.
- Client state via `useTheme()` (`src/lib/theme.ts`,
  `useSyncExternalStore`): returns `{ mode, accent, resolved, setMode, setAccent }`.
  Persisted **locally** in `localStorage` keys `nexus-theme` / `nexus-accent`.
- `mode: "system"` follows `prefers-color-scheme` **live** (media-query listener).
- **Deliberately NOT read from backend `UserSettings.theme`** — the backend
  column defaults to `"dark"` for every account, so it can't distinguish
  "never chose" from "chose dark". Local persistence keeps Paper default intact
  with zero backend changes. Do not "fix" this.
- Theme switching lives in **Settings → Appearance**
  (`src/components/AppearanceSettings.tsx`): Light/Dark/System cards + 4 accent
  swatches (Acid Lime / Signal Teal / Iris Violet / Emerald).

## 3. Design tokens (the only colors allowed)

All colors in components must reference tokens — never hardcoded hex/arbitrary
values. Key tokens (verified in `globals.css`):

| Group | Tokens |
|---|---|
| Surfaces | `--bg-main` (page floor), `--bg-surface` (card), `--bg-surface-elevated`, `--bg-surface-tint` (hover/active), `--bg-glass` / `--bg-glass-hover` |
| Borders | `--border-subtle`, `--border-strong`, `--border-active` |
| Text | `--text-primary` (ink), `--text-secondary`, `--text-muted`, `--text-faint` |
| Accent | `--accent` (fill), `--accent-hover`, `--accent-soft` (tinted fill), `--accent-glow`, `--accent-ink` (text/icons/links on surfaces), `--accent-foreground` (text ON the accent fill) |
| Status | `--status-success`, `--status-warning`, `--status-danger` |
| Specialty | `--grad-brand`, `--grad-hero-floor`, `--inline-code-bg`, `--grid-line`, `--shadow-float-color`, `--btn-shadow` / `--btn-shadow-hover`, `--code-block-bg` |
| Shadcn bridge | `--background/--foreground/--card/--popover/--primary/--muted/--border/--input/--ring`, radii & fonts via the `@theme inline` block (Tailwind v4) |

In JSX the idiom is arbitrary-value classes over the vars:
`bg-[var(--bg-surface)]`, `text-[var(--text-primary)]`,
`border-[var(--border-subtle)]`, `bg-[var(--accent)]`,
`text-[var(--accent-ink)]`, `bg-[var(--accent-soft)]`, etc.

## 4. Hard rules (anti-black-design contract)

1. **Never hardcode design colors in components.** No hex literals, no
   `bg-black` / `text-white` / `bg-zinc-900` etc. on theme surfaces. Use the
   tokens above. (The appearance-preview tiles in `AppearanceSettings.tsx` and
   the fallback `app/global-error.tsx` may use the raw palette values — both are
   deliberate and documented.)
2. **Every component must be readable in BOTH Paper and Void.** A color that
   works on white must also resolve on `#08090a`. Check both themes before
   shipping; invisible-text regressions are the #1 failure mode of this design.
3. **Code blocks stay "terminal dark" in both themes.** `--code-block-bg:
   #0b0d0f` never lightens in Paper; the `.dark-island` class re-declares base
   tokens locally so children resolve correctly. White hover text inside a
   `dark-island` is correct. Do not "fix" it.
4. **Accent-aware means token-aware.** Links/active states use
   `--accent-ink`/`--accent-soft`, never a hardcoded lime hex.
5. **Respect the unset-default.** Lime = no `data-accent` attribute. Don't
   force a `data-accent="lime"` attribute; either is harmless but the canonical
   form is attribute-absent.
6. **Theme is client-local only.** Don't start defaulting from backend
   settings unless a real product decision is made (see §2).
7. **Dialog overlay** (`bg-black/40` in `ui/dialog.tsx`), white text on
   `--status-danger` filled buttons, and the `--shadow-float-color` rgba(0,0,0)
   shadow token are deliberate exceptions — keep them.

## 5. Verified working (audit results)

Audited 2026-09-29 at commit `2a42a8c`:

- CI gates all green: `npx next typegen` → `tsc --noEmit` → `eslint` →
  211 Vitest tests → `next build`.
- Playwright screenshot sweep (landing, signin, `/app` shell + Settings/
  Usage/Knowledge views, mobile 375px) across Paper + Void + lime/cyan/violet
  accents: **zero console errors, zero page errors, zero invisible-text
  elements, zero horizontal overflow**.
- Live behavior confirmed: flipping **Light → Dark via the real Settings →
  Appearance control** toggles `data-theme` on `<html>` instantly; a fresh
  dark+violet session boots pre-themed with no flash; `⌘K` command palette
  opens/closes.
- Computed colors match the spec exactly: Paper body `rgb(250,250,249)`, ink
  `rgb(22,23,26)`; Void body `rgb(8,9,10)`, headings white.

## 6. How to verify future design work (self-check before committing)

```bash
cd frontend
npx next typegen && npx tsc --noEmit
npx eslint src scripts
node "node_modules\vitest\vitest.mjs" run   # Windows: the vitest bin shim is broken
npx next build
```

Then load `/` and `/app` in both themes and look for: unreadable text, stray
hardcoded colors, horizontal scroll on mobile, console errors. The audit
Playwright scripts live under the harness temp dir at audit time — recreate
the checks from §5's checklist.