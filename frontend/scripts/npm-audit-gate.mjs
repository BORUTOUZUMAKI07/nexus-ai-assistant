/**
 * npm-audit-gate.mjs — the CI gate for `npm audit`, with a documented
 * allowlist for advisories that have no fixed release.
 *
 * `npm audit` has no `--ignore-vuln` flag (pip-audit does, which is why the
 * backend needs no equivalent), so an advisory with no patched release forces a
 * choice between turning the gate off and writing this wrapper. The wrapper
 * keeps the gate: it fails on every high/critical advisory EXCEPT the exact
 * ones named in ALLOWED, each carrying the reason its exposure is acceptable.
 * An entry must be deleted the moment an upstream fix ships — an allowlist
 * entry that outlives its advisory is how a real one gets ignored.
 *
 * Run: node scripts/npm-audit-gate.mjs
 */
import { spawnSync } from "node:child_process"
import { pathToFileURL } from "node:url"

/** npm's severity ladder, so the gate can match `--audit-level=high`. */
export const SEVERITY_RANK = { info: 0, low: 1, moderate: 2, high: 3, critical: 4 }

/**
 * Advisories allowed to fail the audit gate. Each MUST have no fixed release
 * (a fixable advisory belongs in the lockfile, not here) and a stated reason
 * that names why the affected path is unreachable or harmless.
 */
export const ALLOWED = new Map([
  [
    "GHSA-vfj7-8cjw-p6xm",
    {
      package: "braces",
      reason:
        "CVE-2026-93687, depth-guard stack overflow in braces <= 3.0.3; no " +
        "patched release exists (upstream advisory first_patched_version: null) " +
        "and 3.0.3 is the latest published version. Reached only through " +
        "eslint-config-next -> @next/eslint-plugin-next -> fast-glob -> " +
        "micromatch, a dev-only lint-time glob matcher: it never ships to " +
        "production and only ever globs this repository's own file names, never " +
        "untrusted input.",
    },
  ],
])

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
 * Pure decision: which blocking advisories are allowed, which are not.
 * Split from the CLI so the gate can be tested without running `npm`.
 */
export function evaluateAuditReport(report, allowed = ALLOWED) {
  const blocking = collectBlockingAdvisories(report)
  const permitted = blocking.filter((a) => allowed.has(a.id))
  const unallowed = blocking.filter((a) => !allowed.has(a.id))
  return { blocking, permitted, unallowed }
}

function runAudit() {
  return process.platform === "win32"
    ? spawnSync("npm audit --json", { encoding: "utf8", shell: true })
    : spawnSync("npm", ["audit", "--json"], { encoding: "utf8" })
}

function main() {
  const result = runAudit()

  if (!result.stdout) {
    console.error("npm-audit-gate: `npm audit --json` produced no output")
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

  const { permitted, unallowed } = evaluateAuditReport(report)

  for (const advisory of permitted) {
    console.log(`npm-audit-gate: allowed ${advisory.id} (${advisory.package}, ${advisory.severity})`)
    console.log(`  reason: ${ALLOWED.get(advisory.id).reason}`)
  }

  if (unallowed.length > 0) {
    console.error(`npm-audit-gate: ${unallowed.length} unallowed high/critical advisory(ies):`)
    for (const advisory of unallowed) {
      console.error(`  - ${advisory.id} ${advisory.package} (${advisory.severity}) ${advisory.url}`)
      if (advisory.title) console.error(`    ${advisory.title}`)
    }
    process.exit(1)
  }

  console.log(`npm-audit-gate: OK — no high/critical advisories beyond the ${ALLOWED.size} documented allowance(s)`)
}

const invokedDirectly =
  Boolean(process.argv[1]) && import.meta.url === pathToFileURL(process.argv[1]).href
if (invokedDirectly) main()
