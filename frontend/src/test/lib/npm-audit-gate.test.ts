/**
 * The npm audit gate's decision logic.
 *
 * The gate is scoped to the dependency graph that ships. `npm audit` also flags
 * dev/build-only packages such as the no-fix picomatch advisory reached through
 * eslint/vitest; those never reach a user, and suppressing them by name used to
 * leave a permanent allowlist that could hide a future production advisory.
 * Instead the gate runs `npm audit --omit=dev` and rejects every high/critical
 * advisory in that report, with no exceptions.
 */
import { describe, it, expect } from "vitest"
import * as auditGate from "../../../scripts/npm-audit-gate.mjs"

const { advisoryId, collectBlockingAdvisories, evaluateAuditReport, AUDIT_ARGS } = auditGate

const bracesAdvisory = {
  url: "https://github.com/advisories/GHSA-vfj7-8cjw-p6xm",
  severity: "high",
  name: "braces",
  title: "braces vulnerable to stack-exhaustion denial of service",
}

const realReport = {
  vulnerabilities: {
    braces: { severity: "high", via: [bracesAdvisory] },
    micromatch: { severity: "high", via: ["braces"] },
    "fast-glob": { severity: "high", via: ["micromatch"] },
    "@next/eslint-plugin-next": { severity: "high", via: ["fast-glob"] },
    "eslint-config-next": { severity: "high", via: ["@next/eslint-plugin-next"] },
  },
}

describe("collectBlockingAdvisories", () => {
  it("counts an advisory once, not once per dependent package", () => {
    const blocking = collectBlockingAdvisories(realReport)
    expect(blocking).toHaveLength(1)
    expect(blocking[0].id).toBe("GHSA-vfj7-8cjw-p6xm")
    expect(blocking[0].package).toBe("braces")
  })

  it("treats a string `via` as a consequence, never as its own advisory", () => {
    // `eslint-config-next` is high only because it depends on the plugin.
    const blocking = collectBlockingAdvisories({
      vulnerabilities: {
        "eslint-config-next": { severity: "high", via: ["@next/eslint-plugin-next"] },
      },
    })
    expect(blocking).toEqual([])
  })

  it("ignores advisories below high", () => {
    const blocking = collectBlockingAdvisories({
      vulnerabilities: {
        something: {
          severity: "moderate",
          via: [{ url: "https://github.com/advisories/GHSA-aaaa-bbbb-cccc", severity: "moderate" }],
        },
      },
    })
    expect(blocking).toEqual([])
  })
})

describe("advisoryId", () => {
  it("prefers the GHSA id from the advisory URL", () => {
    expect(advisoryId(bracesAdvisory)).toBe("GHSA-vfj7-8cjw-p6xm")
  })

  it("falls back to the CVE when there is no GHSA URL", () => {
    expect(advisoryId({ severity: "high", cve: "CVE-2026-12345" })).toBe("CVE-2026-12345")
  })

  it("returns null for a string consequence entry", () => {
    expect(advisoryId("braces")).toBeNull()
  })
})

describe("evaluateAuditReport", () => {
  it("fails on every high advisory, including picomatch", () => {
    const picomatchAdvisory = {
      url: "https://github.com/advisories/GHSA-fc5q-39xc-vjpf",
      severity: "high",
      name: "picomatch",
      title: "picomatch vulnerable to ReDoS through single-branch repeated extglobs",
    }
    const { unallowed } = evaluateAuditReport({
      vulnerabilities: {
        picomatch: { severity: "high", via: [picomatchAdvisory] },
      },
    })
    expect(unallowed.map((a: { id: string }) => a.id)).toEqual(["GHSA-fc5q-39xc-vjpf"])
  })

  it("fails on a critical advisory", () => {
    const { unallowed } = evaluateAuditReport({
      vulnerabilities: {
        evil: {
          severity: "critical",
          via: [{ url: "https://github.com/advisories/GHSA-zzzz-yyyy-xxxx", severity: "critical" }],
        },
      },
    })
    expect(unallowed.map((a: { id: string }) => a.id)).toEqual(["GHSA-zzzz-yyyy-xxxx"])
  })

  it("passes a production graph with no high/critical advisories", () => {
    const { blocking, unallowed } = evaluateAuditReport({
      vulnerabilities: {
        moderate: {
          severity: "moderate",
          via: [{ url: "https://github.com/advisories/GHSA-aaaa-bbbb-cccc", severity: "moderate" }],
        },
      },
    })
    expect(blocking).toEqual([])
    expect(unallowed).toEqual([])
  })
})

describe("gate scope", () => {
  it("audits only production dependencies", () => {
    expect(AUDIT_ARGS).toContain("--omit=dev")
    expect(AUDIT_ARGS).not.toContain("--include=dev")
  })

  it("does not expose a per-advisory allowlist", () => {
    expect("ALLOWED" in auditGate).toBe(false)
  })
})
