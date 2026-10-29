# API contract

Interactive OpenAPI schemas are available at `/docs` and `/openapi.json`. Prefix all routes below with `/api/v1`. Admin-key auth is through X-Admin-Key, not cookies. Local loopback mode is keyless unless ADMIN_KEY is configured. Mutation requests from an unexpected Origin are rejected.

| Method | Path | Behavior |
| --- | --- | --- |
| GET | /health/live | Process health |
| GET | /health/ready | Database connectivity and migration-table readiness |
| GET / POST | /monitors | List / create validated config |
| GET / PATCH | /monitors/{id} | Read / replace preferences and create immutable version; PATCH expects the complete config |
| POST | /monitors/{id}/refresh | 202 with run_id/state/mode; returns existing run on same-day collision |
| GET | /monitors/{id}/runs | Latest 100 runs with diagnostics |
| GET | /monitors/{id}/overview | Date window, counts, daily gaps and quality metadata |
| GET | /monitors/{id}/skills | Evidence/requiredness/alternative-aware skill counts |
| GET | /monitors/{id}/titles | Raw titles, normalized seniority and work arrangement |
| GET | /monitors/{id}/salaries | Explicit disclosure rate and comparable salary groups |
| GET | /monitors/{id}/jobs | Keyset source ID cursor, limit 1–100 and search |
| GET | /jobs/{source}/{source_job_id} | Plain cleaned description, raw snapshot and extraction |
| GET | /monitors/{id}/insights | Deterministic observations with evidence IDs |

Metric endpoints accept `from` and `to` ISO dates (inclusive in monitor timezone, max 366 days); default 28-day window ending today. Skills additionally accept requiredness; salaries additionally accept currency (GBP, USD or EUR). `meta` includes query version, date bounds, eligible/parsed counts, coverage, freshness, stale/small-sample/cap/incomplete-run flags, extraction versions and methodology ID. Unknown ratios are null, never coerced to zero. Failed and partial collections are excluded.

Manual refresh does not perform scraping within the HTTP request. Run IDs and counts are internal; there is no fabricated external provider run or dataset identifier. A missing monitor returns 404 on read, invalid config returns 422, disabled or exhausted budget refresh returns 409, and authentication failures return 401/403. Never expose checkpoint raw data or private settings through an unguarded proxy.

Jobs include completed and partial collections and return `collection_state`, `description_available` and `detail_error` per listing. Overview quality metadata additionally exposes `collected_jobs`, `collected_parsed`, `partial_only_jobs` and `latest_collection_at`; these describe the saved records, independently of eligibility for trends. Partial records remain excluded from statistical aggregates.

`GET /api/v1/source-health` (authenticated) reports source, guest auth mode, state, reason and retry_at. Metric metadata includes source_health. Job records additionally include description_cached and description_fetched_at. The new `listing-coverage-v2` method admits partial runs only when their listing_status explicitly completed; description-specific metrics still show parsed coverage and exclude stale descriptions. Earlier partial runs with unknown listing completeness remain excluded.
