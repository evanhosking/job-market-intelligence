# Operator runbook

## Configuration and authentication

All credentials come from server environment or `.env`, ignored by Git. Never use NEXT_PUBLIC_ for credentials. The only optional public web variable is `NEXT_PUBLIC_API_URL` (API origin only). For remote access set `APP_MODE=deployed`, a random `ADMIN_KEY` of at least 32 characters, `WEB_ORIGIN` to the exact browser origin and an HTTPS API URL. The browser accepts the key in Preferences and holds it in memory. All private reads and writes require it; health endpoints remain public. Use TLS and a restrictive reverse proxy. Cookie auth is not used. Cross-origin mutations are rejected; CORS is exact-origin. Local mode additionally checks client loopback and Host to reduce DNS-rebinding exposure. Bind the local API to 127.0.0.1. A reverse proxy must use deployed mode, not local mode.

One daily refresh per monitor/mode/local day is enforced in the database; repeated calls return the existing run. This plus a hard item cap and global budget bounds collection triggers. A general HTTP request rate limiter is not yet included: configure one at the reverse proxy before remote exposure.

## Daily scheduling

`market worker --live` checks the schedule every ten seconds and processes one queued run. `market worker --once --live` is suitable for an external scheduler every minute; alternatively `market schedule` enqueues only and a separate long-running worker drains the queue. Offline workers omit --live. Each monitor defaults to 06:00 Europe/London. ZoneInfo calculates local wall-clock dates across BST/GMT. At most one daily run per local calendar day and mode is queued, independently of version edits. Downtime catches up only the current local day after its due time; older dates remain missing in the chart. This is a bounded catch-up policy, not reconstructed history.

Sunday's weekly overlap is a separate workload with its own reservation. It waits until the daily run has finished. A daily empty run with successful collection can record zero. Incomplete runs and failed requests never become zero-demand days. Monitor edits create a separate query version; the next date can use that version. UTC-offset comparisons in the database operate on application-generated UTC ISO strings.

PostgreSQL supports multiple workers through row locking and SKIP LOCKED. SQLite is single-process development only. Collection heartbeat lease is two minutes; extraction reserves two hours because its bounded sequential paid calls can take longer. On lease expiration the run becomes interrupted. No uncertain live request is automatically repeated.

## Recovery and diagnostics

```sh
uv run market runs
uv run market replay --run-id RUN_ID
uv run market replay --file tests/fixtures/jobs.json
```

Replay requires an inactive run and a saved checkpoint or explicit JSON file. It never scrapes or calls a paid model. It is idempotent for source identities, observations, snapshots and extractor versions. A replay of a live checkpoint remains partial because its download completeness cannot be established. Replaying an already observed run does not silently replace its observation with altered input. For a fresh source collection wait until the next scheduled date. Do not force database state to complete.

Worker logs carry internal run IDs, state, safe counts and exception class; API response/run diagnostics expose request/page/detail-failure counts and truncation. Credentials and provider response bodies are not logged. Detailed exception text is intentionally omitted to avoid leaking secrets. Schema drift/challenge content fails collection; missing identity records go to quarantine. Daily page counts are sample coverage, not market totals.

If the monthly budget is exhausted, enqueue skips that monitor and logs the reason without preventing already queued work from running. Item and model reservations are conservative and not refunded automatically. Review reservation rows before an explicit operator adjustment. Each new month has a separate UTC calendar-month reservation. A failed queued run may retain unused allocation; this trades capacity for safety.

## Migrations and backups

```sh
uv run alembic current
uv run alembic upgrade head
pg_dump --format=custom --file=market.backup "$PGDATABASE_URL"
pg_restore --dbname="$PGDATABASE_URL" --clean --if-exists market.backup
```

`PGDATABASE_URL` is an operator shell variable holding a native PostgreSQL URI (`postgresql://`, without SQLAlchemy's `+psycopg`). Supply it through your secret tooling; do not commit it. Verify restore into a separate database before replacing any live database. Stop API/worker while migrating/restoring. Store encrypted backups outside the repo. Use version-controlled forward migrations for later schema changes. `alembic downgrade base` reverses the initial migration by deleting all application tables and data; only use it on an explicitly disposable database or after a verified backup.

Raw retention target is 90 days, but pruning automation is deferred. Back up checkpoints and descriptions; do not delete the only source evidence of published metrics. A pruning implementation must either preserve evidence or mark dependent metrics non-auditable. Run logs currently remain in the database. Rotate admin/model credentials by updating server environment and restarting services; no browser credential persistence exists.

## Live data exists but the dashboard is empty

The API loads `.env` only at process startup. Restart it after changing DATABASE_URL or COLLECTION_MODE; a worker launched later may already be using live settings while an older API still serves the demo database. On reload the dashboard selects an available monitor if the previous database's monitor ID is no longer present.

The Jobs explorer includes saved observations from completed and partial runs. Listings identify partial collection and missing descriptions; overview metadata reports collected_jobs, collected_parsed and partial_only_jobs. Trend metrics still use only completed runs. For example, a run may save 25 listing records but retrieve only six descriptions because LinkedIn returned access challenges on 19 detail requests. Those 25 jobs should be inspectable, while the run remains partial and its daily trend is missing. Do not mark a partial run complete merely to populate charts.

## Guest worker version 2

Migration 0002 adds source health and durable per-job description tasks. Apply with `uv run alembic upgrade head` before API/worker startup. No existing jobs, snapshots or observations are deleted. Downgrade 0002 drops only the source-health/cache-task tables, so successful description snapshots remain. Back up before any migration.

Challenge detection considers valid job content before treating an embedded sign-in form as a challenge. Confirmed challenge redirects, 401/403/999 responses or challenge-only pages pause live collection until explicit operator review/resume. Rate limits automatically release after the full Retry-After delay. The worker does not store member account credentials.

New runs record listing_status and enrichment_status separately. Saved listing checkpoints are ingested even if a later search page fails; such searches remain incomplete and do not become healthy zero days. Fresh cached descriptions carry their retrieval date. Old descriptions survive a failed refresh but stale ones are excluded from requirement metrics.

A continuous live worker drains due missing-description tasks with a new, separately budgeted recovery run. It reuses saved listing IDs, refreshes only missing/stale descriptions, and never changes a previously incomplete search to complete. Missing detail retries have a finite attempt cap; not-found and parser drift stop automatic attempts. If a challenge is confirmed use `market source-resume --live` only after reviewing the diagnostic classification. CLI `market refresh --live --limit 50` queues an explicit manual collection; `market retry-details --live --run-id ID --limit 50` queues saved-listing recovery.

## Focused LLM processing

Migration 0003 adds extraction diagnostics. Back up, apply `alembic upgrade head`, and restart the API/worker. Model credentials remain server-only. Use `market extract --dry-run --limit 20` to preview selection metadata, then configure LLM_MODE, LLM_MODEL and LLM_API_KEY locally and run `market extract --live --limit 1` as an initial quality check. This uses saved descriptions, not LinkedIn requests.

LLM calls are claimed durably per snapshot/model/prompt/selection version. Claims reserve one monthly model request before transmission, including requests that time out or fail validation. Interrupted `processing` claims are not automatically retried; investigate before intentionally changing the model/prompt version. No paid schema-repair retry is attempted. Model errors quarantine that attempt; ingestion and previous valid extraction remain available. Provider usage counts and excerpt hashes/offsets are persisted without storing provider response bodies or full prompts. Configure provider-side spend limits separately.

## AI logging and theme refresh

Set LOG_LEVEL=INFO and LOG_FORMAT=json for structured metadata-only audit events. AI events identify selection, no-call decisions, cache status, budget reservations, outbound calls, HTTP status/request IDs, validation stage, usage, result persistence and batch totals. stdout remains machine-readable command results; stderr carries logs. Do not enable raw HTTP wire logging or store log files in Git.

`market extract --rules --limit 100` updates saved description classifications with the current deterministic theme taxonomy and recalculates metrics without any provider requests or source refreshes. Valid existing LLM results remain preferred. The job detail audit shows latest-attempt diagnostics, including failures, independently of the displayed valid result. New model prompt/taxonomy versions have new idempotency keys; a live reprocessing command can therefore incur new calls, within the durable monthly request cap. Earlier provider calls cannot be retroactively assigned request IDs when those were not captured.
