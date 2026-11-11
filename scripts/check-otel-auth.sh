#!/usr/bin/env bash
# ╔═══════════════════════════════════════════════════════════════════════════╗
# ║  NEXUS AI ASSISTANT – OTLP COLLECTOR CREDENTIAL CHECK                      ║
# ╚═══════════════════════════════════════════════════════════════════════════╝
#
# Answers one question: does the deployed collector accept the credentials the
# backend exports with?
#
# Why this exists: OTLP_AUTH_USER / OTLP_AUTH_PASSWORD are `sync: false` in
# render.yaml, so they are typed into two separate Render services by hand and
# can drift. When they do, the collector answers every export with 401 and the
# app's circuit breaker simply stops exporting after three failures — telemetry
# disappears with no visible error in the app.
#
# Both values are secrets. This script never prints them: it compares them
# locally and reports only MATCH/DIFFER plus the HTTP status the collector
# returned. Pass them as environment variables (they stay out of shell history
# and out of this file).
#
# Usage:
#   COLLECTOR_OTLP_AUTH_USER=… COLLECTOR_OTLP_AUTH_PASSWORD=… \
#   BACKEND_OTLP_AUTH_USER=…   BACKEND_OTLP_AUTH_PASSWORD=… \
#   bash scripts/check-otel-auth.sh
#
# Values come from: Render dashboard → nexus-otel-collector → Environment, and
# → nexus-backend → Environment.
#
# Exit: 0 = credentials match and the collector accepts the export; 1 = they
# differ or the export was rejected; 2 = a required variable is missing.
#
# Optional: OTLP_ENDPOINT overrides the collector base URL (default is the
# deployed layer-2 collector).

set -uo pipefail

ENDPOINT="${OTLP_ENDPOINT:-https://nexus-otel-collector.onrender.com}"

required=(
  COLLECTOR_OTLP_AUTH_USER
  COLLECTOR_OTLP_AUTH_PASSWORD
  BACKEND_OTLP_AUTH_USER
  BACKEND_OTLP_AUTH_PASSWORD
)

missing=()
for name in "${required[@]}"; do
  if [[ -z "${!name:-}" ]]; then
    missing+=("$name")
  fi
done
if (( ${#missing[@]} > 0 )); then
  # Names only: the values are exactly what must not be echoed.
  printf 'missing required variable(s): %s\n' "${missing[*]}" >&2
  echo "see the usage block at the top of this script" >&2
  exit 2
fi

collector_pair="${COLLECTOR_OTLP_AUTH_USER}:${COLLECTOR_OTLP_AUTH_PASSWORD}"
backend_pair="${BACKEND_OTLP_AUTH_USER}:${BACKEND_OTLP_AUTH_PASSWORD}"

if [[ "$collector_pair" == "$backend_pair" ]]; then
  echo "credentials: MATCH"
else
  echo "credentials: DIFFER"
fi

# An empty OTLP payload: enough to exercise the receiver's authenticator,
# nothing to store or forward.
code=$(curl -s -o /dev/null -w '%{http_code}' -m 60 \
  -u "$collector_pair" \
  -X POST "$ENDPOINT/v1/traces" \
  -H 'Content-Type: application/json' \
  -d '{"resourceSpans":[]}')

echo "collector accepts export: $code"

status=1
case "$code" in
  200)
    echo "OK: the collector accepted the export, so telemetry is flowing."
    if [[ "$collector_pair" == "$backend_pair" ]]; then
      status=0
    else
      echo "But the pairs differ: copy the collector's pair onto nexus-backend." >&2
    fi
    ;;
  401)
    echo "REJECTED: the collector does not know this pair." >&2
    echo "  - if it was just changed, redeploy nexus-otel-collector (it keeps the old env until then)" >&2
    echo "  - otherwise set nexus-backend's OTLP_AUTH_* to the collector's values" >&2
    ;;
  *)
    echo "UNEXPECTED: the collector is reachable but answered $code." >&2
    echo "  a 502 here means the free instance is still waking; retry in ~15s" >&2
    ;;
esac

exit "$status"
