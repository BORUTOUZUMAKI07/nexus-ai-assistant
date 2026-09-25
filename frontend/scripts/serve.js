/**
 * Local "production" server for the standalone Next.js build.
 *
 * `next start` does not support `output: "standalone"`, so this script mirrors
 * the runner stage of the Dockerfile: lay down `public/` and `.next/static`
 * inside the standalone tree and boot `server.js` on PORT/HOSTNAME.
 */
const { cpSync, existsSync, mkdirSync } = require("node:fs")
const { resolve, join } = require("node:path")
const { spawn } = require("node:child_process")

const root = resolve(__dirname, "..")
const standalone = join(root, ".next", "standalone")
const server = join(standalone, "server.js")

if (!existsSync(server)) {
  console.error("Standalone build not found. Run `npm run build` first.")
  process.exit(1)
}

const copyDir = (src, dest) => {
  if (!existsSync(src)) return
  mkdirSync(dest, { recursive: true })
  cpSync(src, dest, { recursive: true })
}

copyDir(join(root, ".next", "static"), join(standalone, ".next", "static"))
copyDir(join(root, "public"), join(standalone, "public"))

const env = { ...process.env }
if (!env.PORT) env.PORT = "3000"
if (!env.HOSTNAME) env.HOSTNAME = "0.0.0.0"

const child = spawn(process.execPath, [server], {
  cwd: standalone,
  env,
  stdio: "inherit",
})
child.on("exit", (code) => process.exit(code ?? 1))