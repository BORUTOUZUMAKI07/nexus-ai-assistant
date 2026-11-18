import { describe, it, expect } from "vitest"
import { readFileSync } from "node:fs"
import { resolve } from "node:path"

const read = (relative: string) =>
  readFileSync(resolve(process.cwd(), "src", relative), "utf8")

describe("accessibility polish — styles and toaster configuration", () => {
  const css = read("app/globals.css")

  it("disables landing and reveal animations in one reduced-motion block", () => {
    const reduced = css.slice(css.indexOf("@media (prefers-reduced-motion: reduce)"))
    for (const selector of [
      ".reveal-up",
      ".landing-aurora",
      ".landing-shimmer",
      ".landing-marquee",
      ".animate-bounce",
    ]) {
      expect(reduced, selector).toContain(selector)
    }
    // The streaming reductions added by another agent must survive.
    expect(reduced).toContain(".streaming-cursor")
    expect(reduced).toContain(".stream-caret::after")
    expect(reduced).toContain(".stream-dot")
    // Disabled reveal-up animations must not leave the element transparent.
    expect(reduced).toMatch(/\.reveal-up\s*\{[^}]*opacity:\s*1/)
  })

  it("keeps scroll-reveal content visible unless a JS root class is present", () => {
    const base = css.match(/\.sr\s*\{([^}]*)\}/)
    expect(base).not.toBeNull()
    expect(base?.[1]).toContain("opacity: 1")
    expect(css).toMatch(
      /:where\(html\.js,\s*html\.reveal-ready,\s*html\[data-theme\]\)\s*\.sr/
    )
  })

  it("lifts the toaster above the fixed mobile composer without changing desktop", () => {
    const source = read("components/ui/sonner.tsx")
    expect(source).toContain("mobileOffset")
    expect(source).toMatch(/mobileOffset=\{\{\s*bottom:\s*96\s*\}\}/)
    expect(source).toContain('position="bottom-right"')
  })
})
