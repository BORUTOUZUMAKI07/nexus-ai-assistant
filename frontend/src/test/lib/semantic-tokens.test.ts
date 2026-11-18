import { describe, it, expect } from "vitest"
import { readFileSync } from "node:fs"
import { resolve } from "node:path"

const read = (relative: string) =>
  readFileSync(resolve(process.cwd(), "src", relative), "utf8")

describe("semantic surface tokens", () => {
  it("defines a reusable scrim and danger-foreground token in both themes", () => {
    const css = read("app/globals.css")
    const light = css.slice(css.indexOf(":root {"), css.indexOf('[data-theme="dark"]'))
    const dark = css.slice(css.indexOf('[data-theme="dark"]'))

    expect(light).toContain("--overlay-scrim:")
    expect(light).toContain("--danger-foreground:")
    expect(dark).toContain("--overlay-scrim:")
    expect(dark).toContain("--danger-foreground:")
  })

  it("does not hardcode overlay, white-on-danger, or hover-white colours in the shell", () => {
    for (const file of [
      "app/app/page.tsx",
      "components/ui/dialog.tsx",
      "features/chat/ChatInput.tsx",
      "features/chat/ChatArea.tsx",
      "features/chat/ModelComparison.tsx",
      "components/layout/KeyboardShortcuts.tsx",
    ]) {
      const source = read(file)
      expect(source, file).not.toMatch(/bg-black\/(40|50)/)
      expect(source, file).not.toMatch(/hover:bg-white\/10/)
      expect(source, file).not.toMatch(/(^|[^-\w])text-white([^-\w]|$)/)
    }
  })

  it("disables streaming cursor, caret and dot animation under reduced motion", () => {
    const css = read("app/globals.css")
    const reduced = css.slice(css.indexOf("@media (prefers-reduced-motion: reduce)"))
    expect(reduced).toContain(".streaming-cursor")
    expect(reduced).toContain(".stream-caret::after")
    expect(reduced).toContain(".stream-dot")
  })
})
