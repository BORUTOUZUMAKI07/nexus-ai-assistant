/**
 * The mock layer and the route layer must describe the same API.
 *
 * Two failure modes matter, and both previously existed:
 *
 *   * A mock with no route means a component test passes while the request
 *     would 404 in production -- the test asserts a fiction. This was the
 *     original bug: three admin tabs called paths that no route.ts served.
 *   * A route with no mock means the path is untestable from the component
 *     layer. MSW lets the request fall through, and under jsdom there is no
 *     server to fall through to, so a test either errors or silently proves
 *     nothing.
 *
 * The two files this compares are written by hand, so neither suite notices a
 * gap in the other. This test is the seam between them.
 */
import { describe, it, expect } from "vitest"
import { readdirSync, readFileSync, statSync } from "node:fs"
import { join, relative } from "node:path"

const SRC = join(__dirname, "..")

/** Every route.ts under src/app, as the path Next will mount it at. */
function findRoutes(dir = join(SRC, "app")): string[] {
  const out: string[] = []
  for (const entry of readdirSync(dir)) {
    const full = join(dir, entry)
    if (statSync(full).isDirectory()) {
      out.push(...findRoutes(full))
    } else if (entry === "route.ts") {
      out.push("/" + relative(join(SRC, "app"), dir).split("\\").join("/"))
    }
  }
  return out
}

/**
 * Collapse a path to its literal shape.
 *
 * MSW writes `:messageId` where Next writes `[messageId]`, and the two files
 * are not required to use the same parameter name -- MSW matches by position.
 * Comparing raw strings would report every dynamic route as a mismatch.
 */
function shape(path: string): string {
  const segments = path
    .split("/")
    .map((s) =>
      s.startsWith(":") || (s.startsWith("[") && s.endsWith("]")) ? "*" : s,
    )
    .filter((s) => s !== "")
  return "/" + segments.join("/")
}

const ROUTES = findRoutes().map(shape)

function mswPaths(): string[] {
  const src = readFileSync(
    join(SRC, "test", "mocks", "handlers.ts"),
    "utf-8",
  )
  const found = new Set<string>()
  // http.get("/api/x") and http.post(`/api/${id}/y`) both count; the second
  // form is normalised away because its shape is not statically knowable.
  for (const m of src.matchAll(
    /http\.(?:get|post|put|patch|delete)\(\s*"([^"]+)"/g,
  )) {
    found.add(shape(m[1]))
  }
  return [...found]
}

describe("API surface parity", () => {
  const mocks = mswPaths()

  it("has handlers to compare against", () => {
    // A silently empty parse would make both assertions below pass vacuously.
    expect(mocks.length).toBeGreaterThan(30)
    expect(ROUTES.length).toBeGreaterThan(30)
  })

  it("has no mock pointing at a route that does not exist", () => {
    // The false-confidence case: a test that can only pass because MSW answers
    // a path production would 404 on.
    const fictional = mocks.filter((m) => !ROUTES.includes(m))
    expect(fictional).toEqual([])
  })

  it("has no route that a component test cannot reach", () => {
    // Reported per route so a new endpoint shows up by name rather than as a
    // count mismatch.
    const unreachable = findRoutes().filter((r) => !mocks.includes(shape(r)))
    expect(unreachable).toEqual([])
  })
})
