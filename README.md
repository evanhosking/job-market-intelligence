# Fieldwork — Job Market Intelligence

A personal job market dashboard with a deterministic Python collection worker, PostgreSQL provenance, conservative extraction and a Next.js interface. **Collection uses the supplied `linkedin-jobs-cli`, with no Apify dependency or token.** Offline demo is the default. This is a working MVP foundation; see [remaining work](docs/review-and-status.md) before enabling remote operation.

## Quick start without external services

Requirements: Python 3.12+, [uv](https://docs.astral.sh/uv/), Node.js 20.9+ and npm. Run from this repository root. Without a `.env`, a local SQLite database is used for a single-process development demo. PostgreSQL is required for concurrent workers.

```sh
uv sync --locked
uv run alembic upgrade head
uv run market seed
uv run market replay --file tests/fixtures/jobs.json
uv run uvicorn market.api:app --host 127.0.0.1 --port 8000
```

In a second terminal:

```sh
cd apps/web
npm ci
npm run dev
```

Open http://localhost:3000. The green dashboard shows **Demo workspace**, two synthetic postings, explicit evidence, and a missing salary median for insufficient N. API docs: http://localhost:8000/docs. Do not use the synthetic demo to infer actual hiring.

To use the queue instead of file replay, click **Refresh now**, then run in a third terminal:

```sh
uv run market worker --once
uv run market runs
```

Repeated refreshes on the same local day reuse the daily run. A fresh query version becomes eligible on the next calendar day when an older version already ran that day. Switching from demo to live allows a distinct run, but never overlaps an active run for the monitor.

## PostgreSQL setup

```sh
cp .env.example .env
docker compose -f infra/docker-compose.yml up -d db
uv run alembic upgrade head
uv run market seed
uv run uvicorn market.api:app --host 127.0.0.1 --port 8000
```

The example uses a PostgreSQL URL; the SQLite demo and PostgreSQL databases are separate. Start web and worker as above. Compose binds PostgreSQL only to loopback. Stop application processes with Ctrl+C, and PostgreSQL with `docker compose -f infra/docker-compose.yml stop`.

## Live collection

Set `COLLECTION_MODE=live` in your server `.env`, restart the API, queue a run using Refresh now and start:

```sh
uv run market worker --live
```

No LinkedIn login, cookie, proxy or provider token is required. The worker imports `LinkedInPublicClient` from the vendored CLI package; it does not spawn a shell command. Only fixed guest-search and job-detail endpoints are requested. The CLI remains independently usable:

```sh
uv run linkedin-jobs search 'software engineer' --location 'United Kingdom' --limit 10 --format json
```

A run saves immutable effective search parameters. Daily date window is `past24Hours`; Sunday's separately budgeted overlap uses `pastWeek`, including for URL searches. Keyword/location filters are ignored in URL mode. Start with 25 items per run. Public search may be incomplete, blocked or differently ranked. Access challenges, unrecognized markup, repeated pages and detail errors are surfaced. Listing completeness and description coverage are independent: a complete bounded listing phase can support observed-volume/title metrics even when descriptions are partial. An interrupted listing phase remains excluded.

Conservative limits: 1–100 jobs per run, `MONTHLY_ITEM_CAP=3000` reserved items across monitors, `REQUEST_CAP=250` HTTP attempts per run including retries, 1.5-second request delay and a 20-minute collection deadline. Reservations remain consumed after failures because usage may be uncertain. There is no Apify scraping bill; these controls bound workload, not infrastructure charges.

## Extraction

`LLM_MODE=rules` is the offline default and never calls a provider. The curated English rules retain uncertainty and cannot match a language model's full semantic coverage. Versioned results and exact supporting quotes are stored alongside raw snapshots. Missing descriptions and invalid outputs are quarantined without blocking ingestion.

OpenAI structured extraction is available with server-only `LLM_API_KEY`, explicit `LLM_MODEL`, and `LLM_MODE=live`. It uses selected excerpts capped at 8,000 characters, a 3,500 output-token cap and a 45-second request timeout by default. Each snapshot/model/prompt/selection version is attempted at most once automatically, with a durable monthly request reservation made before transmission. Failed model output leaves the existing rules result available. **This is a request/token budget, not dollar accounting.** Configure an OpenAI project spending limit. Offline replay always uses rules. See the focused extraction setup below; live provider behavior still requires a configured key and a small acceptance run.

## Verification

```sh
uv run pytest -q
uv run ruff check src tests db
uv run ruff format --check src tests db
uv run mypy src/market
cd apps/web
npm run typecheck
npm run lint
npm run build
npm run format:check
```

The tests use temporary SQLite databases and HTTP fakes, never LinkedIn or a paid model. [CI](.github/workflows/ci.yml) also supplies PostgreSQL for migration and locking tests. Test databases must be disposable; `TEST_DATABASE_URL` integration tests reset their schema.

## Operations and documentation

- [Document review, scope and remaining work](docs/review-and-status.md)
- [Architecture](docs/architecture.md) and [provider decision](docs/decisions/001-cli-collection.md)
- [Data and metric definitions](docs/data-definitions.md)
- [Runbook: scheduling, recovery, security and backups](docs/runbook.md)
- [API contract](docs/api.md)

The new repository is local; no GitHub remote, public deployment or live scrape was created.

## Guest source health and description recovery

Apply the new migration before restarting API/worker: `uv run alembic upgrade head`. The worker remains guest-only and never stores LinkedIn passwords, session cookies or OAuth tokens. A sign-in widget embedded in an otherwise usable public job page no longer causes a false challenge. HTTP diagnostics retain only response status, path, byte count, content hash and job-content flags, never response headers or challenge bodies.

Confirmed access challenges pause the source; 429 responses defer requests until Retry-After (HTTP-date or seconds). A continuous live worker resumes due missing-description tasks after cooldown without repeating search pages, within monthly and per-run budgets and at most DETAIL_MAX_ATTEMPTS attempts per job. Permanent not-found/schema failures require review. Fresh descriptions are reused for DETAIL_CACHE_HOURS (default 72). Failed refreshes preserve older descriptions, mark them stale, and exclude stale descriptions from requirement metrics.

```sh
uv run market refresh --live --limit 50
uv run market worker --live --once
uv run market retry-details --live --run-id RUN_ID --limit 50
```

`refresh` explicitly queues a separate manual run and creates a new immutable query version if the requested cap changes. `retry-details` creates a bounded recovery run from saved listings, retaining the original listing completeness. Neither command itself issues network requests. The live worker requires both live configuration and `--live`, and can only claim live queue entries; offline workers cannot execute live work.

For a confirmed challenge, inspect source diagnostics first. An operator can explicitly re-enable guest collection with `uv run market source-resume --live`; this does not log in, solve a challenge or bypass access controls. The dashboard and `/api/v1/source-health` report available, cooldown or paused state. Existing jobs remain inspectable during a pause.

## Categorize saved descriptions with OpenAI

The classifier first selects relevant requirements, qualifications, responsibilities, stack, work arrangement and advertised-pay excerpts. It excludes recognized company/benefits/recruitment sections, caps selected text at 8,000 characters and never falls back to blindly sending the complete advert. A short advert consisting entirely of relevant facts can still have most of its text selected. Selection is heuristic: preview it and treat unselected text as unassessed, not evidence of an absent requirement.

The OpenAI request uses strict JSON Schema, bounded output and no tools. Required/preferred/familiarity, employer stack and responsibilities remain distinct. OR alternatives and negation are preserved. Requirements and compensation must quote supplied text; quotes are bounded to 400 characters and checked against source offsets. Known classifications also require evidence. Numeric pay values, currency and period must be supported by their quotes. Schema/evidence failures are quarantined and leave the previous valid rules result available.

Set server `.env` values locally:

```dotenv
LLM_MODE=live
LLM_MODEL=gpt-4.1-mini
LLM_API_KEY=your-local-key
LLM_INPUT_CHAR_CAP=8000
LLM_OUTPUT_TOKEN_CAP=3500
LLM_MONTHLY_REQUEST_CAP=100
```

Keep the key out of chat, Git and public frontend variables. GPT-4.1 mini is an example supporting structured outputs; select a model available to your OpenAI project. The default endpoint is `https://api.openai.com/v1/chat/completions`. Consult [OpenAI structured outputs](https://developers.openai.com/api/docs/guides/structured-outputs?api-mode=chat) and [model documentation](https://developers.openai.com/api/docs/models/gpt-4.1-mini).

```sh
uv run alembic upgrade head
uv run market extract --dry-run --limit 20
uv run market extract --live --limit 20
```

Preview reports only selected character counts, sections and snapshot IDs; it never sends a model request or prints full descriptions. Live extraction reprocesses saved **live** descriptions without scraping and recomputes materialized metrics. Use `--run-id ID` to restrict a batch. Begin with `--limit 1` to inspect quality. Successful, failed or interrupted attempts are cached per snapshot/model/prompt/selection version; repeated commands do not automatically repeat an uncertain paid request.

The monthly model budget is now reserved **per actual attempted request**, atomically before the call, independently of collection budgets. Token usage and selection hashes/offsets are stored, not full model prompts or response bodies. There is no dollar accounting; set an OpenAI project spending limit. In live workers, LLM processing runs after source ingestion commits; the deterministic result remains usable when the model is down. Offline replay never calls OpenAI.

The Jobs detail panel shows categorized facts and expandable short evidence quotes. It requests the original full description only when you expand **Inspect original source description**. Raw JSON and full adverts are not displayed by default.
