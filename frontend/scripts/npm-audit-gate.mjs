/**
 * npm-audit-gate.mjs — the CI gate for advisories in the shipped dependency graph.
 *
 * `npm audit` also reports dev/build-only packages reached through eslint and
 * vitest. Picomatch is the concrete case: its ReDoS advisory has no fixed
 * release, and a name-based allowlist would stay in the repo forever — exactly
 * the shape that eventually hides a real production advisory. Scoping the audit
 * to `--omit=dev` is structural instead: dev tooling is visible in the full
 * report but never reaches a user, while every high/critical advisory in the
 * production graph fails the gate with no exceptions.
 *
 * Run: node scripts/npm-audit-gate.mjs
 */
import { spawnSync } from "node:child_process"
import { pathToFileURL } from "node:url"

/** npm's severity ladder, so the gate can match `--audit-level=high`. */
export const SEVERITY_RANK = { info: 0, low: 1, moderate: 2, high: 3, critical: 4 }

/** The authoritative boundary: advisories in the graph that ships. */
export const AUDIT_ARGS = ["audit", "--omit=dev", "--json"]

/** Extract a stable advisory id from an `npm audit` `via` entry. */
export function advisoryId(via) {
  if (!via || typeof via !== "object") return null
  const ghsa = typeof via.url === "string" ? /GHSA-[\w-]+/.exec(via.url) : null
  if (ghsa) return ghsa[0]
  if (typeof via.cve === "string" && via.cve) return via.cve
  if (via.source) return `npm:${via.source}`
  return null
}

/**
 * Collect the root advisories at or above `high`.
 *
 * A `via` entry that is a string is a *consequence* — "this package is high
 * because it depends on the package named here" — not an advisory of its own,
 * so only object entries (which carry the advisory metadata) are counted.
 */
export function collectBlockingAdvisories(report) {
  const advisories = new Map()
  for (const vuln of Object.values(report.vulnerabilities || {})) {
    for (const via of vuln.via || []) {
      if (!via || typeof via !== "object") continue
      if ((SEVERITY_RANK[via.severity] ?? 0) < SEVERITY_RANK.high) continue
      const id = advisoryId(via)
      if (!id || advisories.has(id)) continue
      advisories.set(id, {
        id,
        package: via.name || vuln.name || "unknown",
        severity: via.severity,
        title: via.title || "",
        url: via.url || "",
      })
    }
  }
  return [...advisories.values()]
}

/**
 * Pure decision: which production advisories block the gate.
 * Split from the CLI so the gate can be tested without running `npm`.
 */
export function evaluateAuditReport(report) {
  const blocking = collectBlockingAdvisories(report)
  return { blocking, unallowed: blocking }
}

function runAudit() {
  if (process.platform === "win32") {
    return spawnSync(["npm", ...AUDIT_ARGS].join(" "), { encoding: "utf8", shell: true })
  }
  return spawnSync("npm", AUDIT_ARGS, { encoding: "utf8" })
}

function main() {
  const result = runAudit()

  if (!result.stdout) {
    console.error("npm-audit-gate: `npm audit --omit=dev --json` produced no output")
    if (result.stderr) console.error(result.stderr.trim())
    process.exit(1)
  }

  let report
  try {
    report = JSON.parse(result.stdout)
  } catch {
    console.error("npm-audit-gate: could not parse `npm audit --json` output")
    console.error(result.stdout.slice(0, 2000))
    process.exit(1)
  }

  const { unallowed } = evaluateAuditReport(report)

  if (unallowed.length > 0) {
    console.error(`npm-audit-gate: ${unallowed.length} high/critical production advisory(ies):`)
    for (const advisory of unallowed) {
      console.error(`  - ${advisory.id} ${advisory.package} (${advisory.severity}) ${advisory.url}`)
      if (advisory.title) console.error(`    ${advisory.title}`)
    }
    process.exit(1)
  }

  console.log("npm-audit-gate: OK — no high/critical advisories in production dependencies")
}

const invokedDirectly =
  Boolean(process.argv[1]) && import.meta.url === pathToFileURL(process.argv[1]).href
if (invokedDirectly) main()
