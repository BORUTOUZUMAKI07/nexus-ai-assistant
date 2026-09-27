# Indexing backlog (async file indexing)

Alert: `NexusIndexingBacklog` — **PLANNED**. Needs the Postgres/queue exporter
noted in `docs/slo.md` §7 before it can fire. Manual monitoring meanwhile:
`SELECT count(*) FROM files WHERE index_status = 'PENDING';`

## Background
`POST /files/upload` with `ASYNC_INDEXING=1` returns **202 Accepted** with a
`Location: {API_V1_PREFIX}/files/{id}/index-status` header, then dispatches a
`file.index_requested` domain event through the `EventPublisher` seam. Today the
only route is Celery `tasks.process_file_indexing` (per
`EVENT_TASK_ROUTING`); an outbox adapter can replace it later with zero call-site
changes.

The **SLO contract is freshness**: a file should leave `PENDING` (succes or
fail) quickly. Alert on files stuck pending or on the publisher failing to
dispatch.

## Symptoms
- Files stuck on `PENDING` in `files.index_status`.
- `202` uploads never reach `COMPLETED`; `/files/{id}/index-status` keeps
  returning `status="pending"`.
- Worker logs show the task erroring, or — worse — no worker consuming the
  dispatch (event published to a queue nobody reads).

## Triage
1. **Is the event dispatched?** `celery inspect registered` for
   `process_file_indexing`; check the broker for the `file.index_requested`
   route. If the publisher returns `dispatched=False`, the event never left the
   API process.
2. **Is the task failing?** Worker logs: parse errors, vector-store timeouts,
   or dead-letter after retries. Check `TaskState`/Redis result backend.
3. **Is a file corrupted?** Test the file locally (re-download from storage);
   document the failure path so a bad PDF doesn't wedge the queue (poison
   message).

## Resolution
| Cause | Fix |
| --- | --- |
| Worker down / queue not consumed | Restart worker; confirm routing key `file.index_requested` → `tasks.process_file_indexing` matches `EVENT_TASK_ROUTING` |
| Provider/vector-store timeout | Raise task retry backoff; the task already tolerates optional `payload` kwargs — keep payloads idempotent |
| Poison file | Catch and mark `index_status='FAILED'` with `index_error`; never loop retries on a deterministic parse error |
| Publisher seam broken | The seam is synchronous-ish at upload time; check `CeleryPublisher` connection — it must fail closed loudly (logs) so a silent no-op doesn't strand files |

## Prevention
- A load-tested async upload path (`backend/loadtests/locustfile.py`) should
  include the `202` + `Location` + `index-status` polling flow.
- Watch `nexus_requests_total{operation="file.upload"}` vs. completed indexes in
  the indexing dashboard (roadmap exporter).