/**
 * Structural contract for the two Playwright lanes.
 *
 * The default suite is deterministic and mocks the API, including a fake login.
 * That is useful for UI behaviour but says nothing about whether a real account
 * can sign in, so a separate live lane uses real credentials against the hosted
 * app. Mixing the two — or leaving an ambiguously named helper that plants a
 * fake cookie — makes a mocked suite look like real auth coverage.
 * These guards keep the boundary explicit.
 */
import { readFileSync, readdirSync } from "node:fs"
import { resolve } from "node:path"
import { describe, expect, it } from "vitest"

const read = (path: string) => readFileSync(resolve(process.cwd(), path), "utf8")

describe("Playwright harness contract", () => {
  it("names the deterministic auth helpers as mocks", () => {
    const fixtures = read("e2e/fixtures.ts")
    expect(fixtures).toContain("export function setupMockAuthRoutes")
    expect(fixtures).toContain("export async function setMockAuthState")
    expect(fixtures).not.toMatch(/\bsetupAuthMocks\b/)
    expect(fixtures).not.toMatch(/\bsetAuthState\b/)
  })

  it("uses only mock-named auth helpers in the deterministic specs", () => {
    const specs = readdirSync(resolve(process.cwd(), "e2e")).filter(
      (name) => name.endsWith(".spec.ts") && name !== "live-auth.spec.ts"
    )
    expect(specs.length).toBeGreaterThan(0)
    for (const spec of specs) {
      const source = read(`e2e/${spec}`)
      expect(source, `${spec} still uses an ambiguous auth helper`).not.toMatch(
        /\bsetupAuthMocks\b|\bsetAuthState\b/
      )
    }
  })

  it("keeps the live auth spec out of the default mocked suite", () => {
    const config = read("playwright.config.ts")
    expect(config).toMatch(/testIgnore:\s*\[[^\]]*live-auth\.spec\.ts/)
  })

  it("drives a production build, not the dev overlay", () => {
    const config = read("playwright.config.ts")
    expect(config).toContain("npm run build && npm run start")
    expect(config).toMatch(/timeout:\s*300000/)
    expect(config).not.toMatch(/command:\s*"[^"]*next dev/)
  })

  it("runs only live auth against the hosted app without a local webServer", () => {
    const config = read("playwright.live.config.ts")
    expect(config).toContain("E2E_LIVE_BASE_URL")
    expect(config).toContain("live-auth.spec.ts")
    expect(config).not.toMatch(/\bwebServer\s*:/)
  })

  it("exposes an npm script for the live lane", () => {
    const pkg = JSON.parse(read("package.json")) as {
      scripts: Record<string, string>
    }
    expect(pkg.scripts["test:e2e:live"]).toContain("playwright.live.config.ts")
  })

  it("uses real credentials and no route mocks in the live auth spec", () => {
    const spec = read("e2e/live-auth.spec.ts")
    expect(spec).toContain("E2E_TEST_EMAIL")
    expect(spec).toContain("E2E_TEST_PASSWORD")
    expect(spec).not.toMatch(/page\.route|mockApi|setMockAuthState/)
  })

  it("gives the landing page an explicit readiness marker", () => {
    expect(read("src/app/page.tsx")).toContain('data-testid="landing-ready"')
  })

  it("waits for that marker in the deterministic landing assertions", () => {
    const spec = read("e2e/auth.spec.ts")
    const waits = spec.match(/getByTestId\("landing-ready"\)/g) ?? []
    expect(waits.length).toBeGreaterThanOrEqual(2)
  })

  it("plants mock auth as a session cookie, not a script that survives logout", () => {
    const fixtures = read("e2e/fixtures.ts")
    const start = fixtures.indexOf("export async function setMockAuthState")
    expect(start).toBeGreaterThan(-1)
    const helper = fixtures.slice(start)
    expect(helper).toContain("page.context().addCookies")
    expect(helper).not.toContain("addInitScript")
  })

  it("accepts the confirmation dialog in destructive UI journeys", () => {
    for (const spec of ["e2e/conversations.spec.ts", "e2e/files.spec.ts"]) {
      expect(read(spec)).toContain('page.once("dialog"')
    }
  })

  it("registers Prism grammars through PrismLight with real grammar files", () => {
    // The previous loader read `registerLanguage` off the package namespace
    // (undefined) and imported three grammar names that do not exist, so the
    // first code block crashed the production workspace.
    const source = read("src/features/chat/ChatArea.tsx")
    expect(source).toContain("PrismLight.registerLanguage.bind")
    expect(source).not.toContain("modAny.registerLanguage")
    expect(source).toContain('html: "markup"')
    expect(source).toContain('shell: "shell-session"')
    expect(source).toContain('xml: "markup"')
    expect(source).toContain("return { default: SyntaxHighlighterBlock }")
    expect(source).not.toMatch(/return SyntaxHighlighterBlock;/)
  })
})
