#!/usr/bin/env bash
# ╔═══════════════════════════════════════════════════════════════════════════╗
# ║  NEXUS AI ASSISTANT — INDEPENDENT EXTERNAL WAKE BACKSTOP                   ║
# ╚═══════════════════════════════════════════════════════════════════════════╝
#
# Why this exists: GitHub's scheduler has been observed to report zero
# `event=schedule` runs while manual dispatch still works. The 5-minute GitHub
# cron remains the PRIMARY heartbeat, but a second, independent scheduler can
# run this script so a stalled GitHub scheduler does not let the Free backend
# suspend. The two schedulers do not depend on each other or share credentials.
#
# LOCKED DECISION: this backstop probes ONLY the user-facing backend. The OTLP
# collector is deliberately NOT a recurring target — two always-on Free
# services need ~1,460 of the workspace's 750 monthly Free instance hours and
# Render then suspends every Free service until the next month. To wake the
# collector during an incident, dispatch the GitHub workflow with
# `wake_collector=true` (see .github/workflows/keepalive.yml).
#
# Idempotent: GET /health is side-effect free; any number of runs are safe.
#
# Authentication: this file contains NO secret. One optional HTTP header can be
# supplied at runtime via KEEPALIVE_HEADER (a full "Name: value" string, e.g.
# 'X-Nexus-Keepalive-Token: <token>'). It is attached to the probe but NEVER
# echoed — only HTTP status codes are logged. Without it the public /health
# endpoint is probed unauthenticated. A fixed, non-secret attribution header
# (X-Nexus-Keepalive-Probe) is always sent so receiver logs can identify this
# traffic as `external_backstop`.
#
# Usage from any external scheduler (cron box, CI of another account, etc.):
#   KEEPALIVE_HEADER='X-Nexus-Keepalive-Token: <token>' \
#     bash scripts/keepalive-backstop.sh
#
# Optional overrides (defaults are the deployed services):
#   BACKEND_HEALTH_URL       probe URL (default: deployed backend /health)
#   KEEPALIVE_HEADER         optional full auth header; never stored here
#   KEEPALIVE_MAX_ATTEMPTS   cold-start poll attempts (default: 12)
#   KEEPALIVE_REQUEST_TIMEOUT  per-request timeout in seconds (default: 20)
#   KEEPALIVE_POLL_INTERVAL  seconds between attempts (default: 10)
#
# Exit: 0 = the backend answered 200 within the bounded poll window; 1 = it did
# not. Worst case is 12 * (20s + 10s) ≈ 6 minutes.

set -uo pipefail

BACKEND_URL="${BACKEND_HEALTH_URL:-https://nexus-backend-owq5.onrender.com/health}"
MAX_ATTEMPTS="${KEEPALIVE_MAX_ATTEMPTS:-12}"
REQUEST_TIMEOUT="${KEEPALIVE_REQUEST_TIMEOUT:-20}"
POLL_INTERVAL="${KEEPALIVE_POLL_INTERVAL:-10}"
KEEPALIVE_HEADER="${KEEPALIVE_HEADER:-}"

echo "entry_point: external_backstop"
echo "trigger_event: external_scheduler"

# Always send a non-secret attribution header; append the caller's auth header
# only when one was supplied. Keeping this an array avoids an empty expansion
# under `set -u` on older bash releases.
curl_headers=(-H "X-Nexus-Keepalive-Probe: external_backstop")
if [[ -n "$KEEPALIVE_HEADER" ]]; then
  curl_headers+=(-H "$KEEPALIVE_HEADER")
fi

probe() {
  local label="$1" url="$2" expected="$3"
  local code="000"
  local attempt
  for attempt in $(seq 1 "$MAX_ATTEMPTS"); do
    code=$(curl --silent --show-error --max-time "$REQUEST_TIMEOUT" \
      -o /dev/null -w '%{http_code}' "${curl_headers[@]}" "$url" || true)
    echo "${label} attempt ${attempt}/${MAX_ATTEMPTS}: HTTP ${code}"
    if [[ "$code" == "$expected" ]]; then
      echo "${label} awake (HTTP ${code})"
      return 0
    fi
    if [[ "$attempt" -lt "$MAX_ATTEMPTS" ]]; then
      sleep "$POLL_INTERVAL"
    fi
  done
  echo "${label} did not wake after ${MAX_ATTEMPTS} attempts (last status: ${code})" >&2
  return 1
}

status=0
# The ONLY recurring target. The collector is intentionally absent: see the
# locked-decision note at the top of this file.
probe backend "$BACKEND_URL" "200" || status=1
exit "$status"
