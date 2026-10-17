/**
 * The npm audit gate's decision logic.
 *
 * `npm audit --audit-level=high` is the CI gate, but it has no `--ignore-vuln`
 * equivalent, so a no-fix advisory can only be tolerated through an allowlist.
 * The wrapper's value depends entirely on it failing for every advisory NOT on
 * that list, so the interesting cases are the ones it must still reject: an
 * unknown high advisory, and a high `via` *string* (a transitive consequence)
 * that must never be mistaken for a root advisory.
 */
import { describe, it, expect } from "vitest"
import {
  advisoryId,
  collectBlockingAdvisories,
  evaluateAuditReport,
  ALLOWED,
} from "../../../scripts/npm-audit-gate.mjs"

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
  it("permits the documented no-fix advisory", () => {
    const { permitted, unallowed } = evaluateAuditReport(realReport)
    expect(permitted.map((a: { id: string }) => a.id)).toEqual(["GHSA-vfj7-8cjw-p6xm"])
    expect(unallowed).toEqual([])
  })

  it("fails on an unknown high advisory (the allowlist is not blanket)", () => {
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
})

describe("ALLOWED", () => {
  it("every allowance names its package and a reason", () => {
    for (const [id, entry] of ALLOWED) {
      expect(id).toMatch(/^GHSA-|^CVE-|^npm:/)
      expect(entry.package).toBeTruthy()
      expect((entry.reason || "").length).toBeGreaterThan(40)
    }
  })
})
