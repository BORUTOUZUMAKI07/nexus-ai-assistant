# Runbook: scheduled runs are missing or delayed

**When to use:** the GitHub Actions API shows `event=schedule` with
`total_count=0` for `Keep Render warm` (or `Nightly`), yet `workflow_dispatch`
of the same workflow succeeds. Observed live on 2026-11-18 alongside delayed
nightly runs.

## What it means

GitHub's scheduler is **not a service-level guarantee**. A zero count for
`event=schedule` means GitHub did not record a scheduled trigger; it does **not**
mean the workflow is broken. Manual dispatch succeeding proves the workflow
definition, secrets and Render endpoints are fine. Do **not** react by moving
the backend to a paid Render plan — the locked decision is GitHub cron as
primary plus an independent external backstop.

Always-on cost constraint (why there is only one warmed service): one Free web
service is ~730 h/month; two are ~1,460 h, above the workspace's **750** h
budget, after which Render suspends every Free service until the next month
(`render.yaml:150-157`, `docs/deployment-guide.md:293-301`). The **backend** is
the only scheduled target because it is user-facing and the BFF times out at
~55s on a cold start. The collector is woken only on demand.

## 1. Attribute the runs you can see

Every keepalive job logs two lines at the top of its step:

```
entry_point: github_actions
trigger_event: schedule        # or workflow_dispatch
```

The probes also send `X-Nexus-Keepalive-Probe: github_actions` so Render/OTLP
receiver logs can tell GitHub traffic from the backstop's
`X-Nexus-Keepalive-Probe: external_backstop`. `trigger_event` is **recorded,
never used as an `if:` gate** — gating on the scheduler's own report would skip
the heartbeat precisely when the scheduler misreports. The backstop logs
`entry_point: external_backstop` / `trigger_event: external_scheduler`.

For Nightly, the same `trigger_event` line appears in each job's first step, so
a delayed nightly can be attributed without assuming the scheduler is healthy.

## 2. Immediate mitigation (manual)

1. Dispatch the workflow manually and confirm the backend log ends with
   `Backend awake (200 from /health)`.
2. If telemetry must be collected now, dispatch it again with
   **"Incident-only: also wake the OTLP collector"** checked. Expect
   `Collector awake (401 Unauthorized from basic auth, as expected)` — 401 is
   the awake signal for a basic-auth-protected receiver, not a failure.
3. Do not leave `wake_collector` on: it is incident-only, not a cron target.

## 3. Stand up the independent backstop (one-time)

Pick **one** of the two options below. Both target only the backend
`https://nexus-backend-owq5.onrender.com/health`; a recurring collector wake
would breach the 750 h budget.

### Option A — copy-paste HTTP monitor (Cron-job.org or UptimeRobot)

Both services can issue an HTTP GET with a custom header. Configure them as a
**heartbeat**, not an alert-as-failure check (a single cold-start 5xx is
expected; set "alert after 2 consecutive failures" where supported).

| Field | Value |
| --- | --- |
| URL | `https://nexus-backend-owq5.onrender.com/health` |
| Method | `GET` |
| Interval | 5 minutes (match the GitHub cron) |
| Custom header 1 | `X-Nexus-Keepalive-Probe: external_backstop` |
| Custom header 2 (optional) | `X-Nexus-Keepalive-Token: <TOKEN_YOU_SUPPLY>` |
| Success condition | HTTP `200` |

Put `<TOKEN_YOU_SUPPLY>` in the monitor's secret/header field only. **Never**
commit it to this repo. Leave header 2 out if `/health` is not protected —
`X-Nexus-Keepalive-Probe` alone identifies the traffic. A plain HTTP monitor
does not do bounded cold-start polling; the next 5-minute run is the retry.

### Option B — script from any external scheduler (preferred for bounded polling)

`scripts/keepalive-backstop.sh` probes the backend up to 12 times (20s request
timeout, 10s apart — ~6 minutes worst case), which covers a Render cold start.
It is idempotent and takes its optional auth header from the environment; there
is no secret in the file and the value is never logged.

```bash
# On the external host, from the repo checkout:
# Every 5 minutes; store the token in that host's environment, not in git.
*/5 * * * * cd /path/to/nexus\ ai-assistant && \
  KEEPALIVE_HEADER='X-Nexus-Keepalive-Token: <TOKEN_YOU_SUPPLY>' \
  bash scripts/keepalive-backstop.sh >> /var/log/nexus-keepalive.log 2>&1
```

With no header needed:

```bash
*/5 * * * * cd /path/to/nexus\ ai-assistant && \
  bash scripts/keepalive-backstop.sh >> /var/log/nexus-keepalive.log 2>&1
```

Override endpoints/timeouts if the deployment changes:
`BACKEND_HEALTH_URL`, `KEEPALIVE_MAX_ATTEMPTS`, `KEEPALIVE_REQUEST_TIMEOUT`,
`KEEPALIVE_POLL_INTERVAL`. Exit 0 = backend answered 200; exit 1 = it did not.

**Coverage check:** the backstop's target set `{backend}` must stay a subset of
the workflow's scheduled target set `{backend}`. The structural tests in
`backend/tests/infra/test_keepalive_workflow.py` fail if a recurring collector
probe is added to either side or if two services land on the cron.

## 4. Verify

- Workflow run log contains `entry_point: github_actions` and the real
  `trigger_event`.
- Backstop log contains `entry_point: external_backstop` and ends with
  `backend awake (HTTP 200)`.
- GitHub API (`.../actions/workflows/keepalive.yml/runs?event=schedule`) may
  still show `total_count=0`; that is the caveat being worked around, not a
  pass/fail signal for the heartbeat.

## 5. Escalation

- **Both schedulers stop waking the backend** → treat as SEV1 using
  [full outage](full-outage.md); check Render's status and the service's own
  logs before changing the workflow.
- **GitHub only** → report via GitHub Support / status page; the external
  backstop is the intended compensation, no code change needed.
- **Empty New Relic UI while the backend is warm** → see
  [New Relic empty dashboard](new-relic-empty-dashboard.md). Note the collector
  is *not* continuously warm by design; its first batch after idle is dropped
  and export resumes on the next one.
