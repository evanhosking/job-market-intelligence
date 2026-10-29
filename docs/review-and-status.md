# Review of the supplied implementation documents

Reviewed the full CODEX_BUILD_PROMPT.md and the text, including tables, of JOB_MARKET_INTELLIGENCE_IMPLEMENTATION_PLAN.docx supplied in the parent workspace. The documents agree on a deterministic collection pipeline, immutable query versions, auditable extraction and explicit statistical populations. Their original Apify choice is superseded by the user's instruction to integrate linkedin-jobs-cli. The originals are preserved.

## Changes to the provider design

| Original requirement | Implemented replacement |
| --- | --- |
| Apify Actor and API credentials | Local vendored linkedin-jobs-cli library using public guest search |
| Remote Actor run and dataset ID | Internal durable collection run, effective parameters and saved raw checkpoint |
| Actor polling and webhooks | Worker executes the CLI client; database leases bound ownership; no webhook surface |
| Dataset pagination | CLI guest-search pagination, duplicate-page detection, per-job detail enrichment |
| Paid Actor item/spend limits | Monthly reserved item budget, per-run HTTP attempt cap, delay and deadline |
| Replay of provider dataset | Saved JSON checkpoint or explicit offline fixture replay |

The public CLI has weaker guarantees than a provider dataset: no authoritative total, no durable remote run to resume, incomplete detail fields, potential search challenges, and no promise of historic or exhaustive coverage. A saved partial download cannot prove completeness. Its replay remains partial and cannot fabricate a zero-result healthy day.

## Implemented foundation

- Independent local Git repository, lockfiles, separate API and worker processes, Next.js dashboard, PostgreSQL Compose and frozen reversible Alembic migration.
- Monitor create/edit, immutable versions, validated structured/URL inputs, once-per-local-day queue dedupe and bounded daily catch-up. Sunday overlap and deterministic UTC/local grouping.
- Vendored CLI extensions also applied to the original sibling CLI: request budgets, checkpoint callback, lease heartbeat hook, challenge detection, strict unknown-markup handling, repeated-page and detail-error diagnostics, bounded Retry-After.
- Canonical source identity, semantic snapshots, per-query observations, quarantine, versioned extraction, curated aliases, evidence mentions, compensation disclosures and reproducible stored metric snapshots.
- Offline rules and experimental JSON-schema LLM adapter; paid calls disabled by default; no agent on the ingestion path.
- Overview, skills, titles/seniority/work arrangement, salaries, jobs/evidence, deterministic insights, monitor preferences and run history UI. Loading, empty, error, stale, small-sample, cap and demo states.
- Authentication boundary for deployed mode; loopback restriction locally; origin/host checks; escaped text rendering; limits on refresh workloads.

## Explicit remaining work

1. PostgreSQL concurrency tests are included for CI but require PostgreSQL; only SQLite migration/replay paths and backend/CLI tests were executed on this machine. Multiworker behavior is unsupported on SQLite.
2. Visual browser verification is outstanding because the available computer-use browser service timed out. Production compilation, TypeScript and lint checks were run; those do not prove visual or mobile accessibility.
3. Live LinkedIn collection and the paid LLM adapter were not exercised. A small operator-initiated live acceptance run is needed before routine use. The API never initiates live work synchronously.
4. Extraction is conservative English pattern matching by default. Arbitrary requirement semantics, multilingual handling, calibrated confidence and comprehensive degree/negation logic need broader fixtures. Low-confidence clauses are not promoted into strict requirements. All model quotes are validated, but a quote alone cannot establish semantic truth.
5. Period-over-period comparisons, rolling series of skill prevalence and statistical intervals remain unimplemented. The UI reports insufficient comparisons instead of speculative percentage changes. Currently supported windows select distinct successfully observed jobs, rather than reconstructing a historic labor-market census.
6. Agent explanations are deferred; deterministic insights are implemented. No autonomous agent, arbitrary SQL tool or scrape expansion is exposed.
7. Full provider token/cost diagnostics, dollar accounting and selective paid model reprocessing are deferred. Hard request/input/output caps and provider-side spending ceilings bound the experimental adapter. Replay uses rules only.
8. Retention/prune automation, rich operational alarms, raw HTML field drift fingerprints and durable per-page resume cursors are deferred. Saved checkpoints are bounded; replay is possible without re-scraping. Do not delete provenance for published metrics.
9. The UI uses in-memory navigation and filters; URL-persisted filters, advanced sort/filter combinations, historical query-version selection and employer concentration analysis remain follow-up work. Jobs are keyset-paginated over the selected date cohort; arbitrary search currently filters the bounded cohort in Python.
10. Production hosting, TLS, reverse proxy configuration and operational credential management are owner choices. No public deployment is configured.

## Smallest next milestone

Run PostgreSQL CI, visually inspect desktop/mobile UI, then perform one explicitly enabled live collection with a 10–25-item monitor. Inspect checkpoint diagnostics and evidence before enabling scheduled live refreshes. Implement comparable complete 7-day windows next, with extraction-version and collection-coverage guards.

## Verification performed for this handoff

- 32 backend/vendored CLI tests passed; one PostgreSQL integration test skipped because no disposable PostgreSQL service is available locally.
- All 11 tests in the original sibling linkedin-jobs-cli also passed with its own source on PYTHONPATH.
- Ruff lint and formatting, mypy, frontend TypeScript, ESLint, Prettier and Next.js production build passed.
- Fresh SQLite Alembic upgrade, downgrade and re-upgrade succeeded; Alembic check reports no schema drift.
- Fixture replay twice produced zero additional extractions on the second replay. The offline smoke test asserts stable job, snapshot, observation, extraction and metric counts.
- Running API readiness returned `{"status":"ready"}`. Live LinkedIn and paid LLM requests were not made. The browser service was unavailable/timed out, so rendered visual and interaction QA is unverified.

## Guest worker improvements after live use

Implemented structural challenge classification, typed failure reasons, Retry-After cooldowns, persistent source pauses and per-job detail tasks. A usable public job response with an embedded sign-in form is accepted; confirmed access denial pauses the guest source. No LinkedIn account credentials, browser sessions or OAuth integration were added.

Listing completeness and enrichment coverage are independent. Successful bounded listing stages can support observed-volume/title metrics while missing descriptions remain excluded from description-based denominators. Unknown/incomplete listing stages still produce trend gaps. Saved partial listings survive later page failures. Cached descriptions retain their retrieval date and stale descriptions survive failures without being promoted into fresh requirement metrics. Missing details resume from saved IDs within budgets; the worker can drain due tasks without repeating searches. Live and offline queue entries are isolated and live execution requires explicit authorization.

Migration 0002 was applied to the live SQLite database after a backup and verified with upgrade/downgrade/re-upgrade on a disposable database. Tests cover the source failure paths and 47 tests passed, with the PostgreSQL test skipped locally; backend lint, formatting and mypy and frontend typecheck/lint/format/build passed. The sibling CLI's 11 tests also passed. Browser visual inspection remains unavailable in this environment.

The requested post-change live refresh completed a bounded sample of 50 distinct valid listings (string source ID, title, company and canonical LinkedIn job URL checked). Eleven descriptions were available from retrieval/cache. The listing stage completed and is visible in dashboard listing metrics; enrichment remains partial because LinkedIn returned HTTP 429. The worker stopped at the rate limit and retained the cooldown; it did not log in or bypass the restriction. An earlier 100-item attempt saved 50 listings before a sixth-page rate limit; those checkpoint records were recovered without claiming search completeness.

## Focused description categorization

Added relevant-section selection, strict schema-constrained OpenAI extraction, short quote validation, per-classification evidence, source offset resolution, pay amount/currency/period checks, durable per-attempt model budget reservations, usage/selection diagnostics and saved-description reprocessing commands. The Jobs panel now shows structured facts and categories with expandable evidence; full source descriptions are fetched only on demand, and raw extraction JSON is not dumped into the UI.

The real saved corpus contains 11 nonempty descriptions. The local preview selected relevant excerpt sets for all 11 without sending any provider requests. Model-backed tests use fake HTTP responses and verify no full-advert fallback, marketing removal, strict schema, exact evidence provenance, refusal handling, budget exhaustion and idempotency. The user chose OpenAI and will configure the API key locally; no live OpenAI parsing has been claimed without a configured key/model.

Focused extraction verification: 59 tests passed, one PostgreSQL integration test skipped locally. Backend lint/format/type checks and frontend lint/format/type checks/production build passed. Migration 0003 upgrade/downgrade/re-upgrade was verified on a disposable SQLite database and the live database was migrated after backup. The actual job-detail API was checked to omit both full descriptions and raw payloads by default. The preview selected 31,633 characters from 48,148 across 11 saved descriptions (about 34% excluded). OpenAI requests were tested with HTTP fakes only; the local API key/model are still unconfigured.

## AI audit logging and capability themes

Added metadata-only JSON audit events for preview/rules/live decisions, excerpt selection, budget reservation/exhaustion, cache hits, request start/response/completion/failure, validation stage, usage, persistence/quarantine and batch totals. Correlation includes batch/run/job/snapshot/extraction/call and provider request IDs. Tests verify that API keys, raw descriptions, endpoint queries, response bodies and unsupported logging fields are excluded. Latest AI-attempt diagnostics remain visible independently of the preferred valid result.

Expanded deterministic recognition and the model prompt/schema with `skills-v2`: AI engineering (assisted development/output validation, applied AI, ML, platforms/frameworks), API and system design, and broader languages, delivery, data, quality, security and collaboration themes. Dashboard/API grouping preserves candidate/stack/responsibility context, alternative groups and negation. Theme counts deduplicate jobs and context counts can overlap. Historical valid LLM results are preserved when local rules are refreshed.

Reprocessed 16 saved descriptions with the new deterministic rules with zero paid provider calls and no source refresh. The current parsed cohort shows AI engineering evidence in 10 of 16 descriptions and API/system-design evidence in 12 of 16. These are observations within the parsed subset, not whole-market prevalence. The expanded suite passes 68 tests with one PostgreSQL integration test skipped locally; backend checks and frontend production build pass. Existing browser visual-verification limitations remain.
