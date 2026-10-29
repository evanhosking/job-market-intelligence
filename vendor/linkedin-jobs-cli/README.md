# LinkedIn Jobs CLI

A clean-room command-line scraper for LinkedIn's logged-out public jobs search. It does not use Apify, a LinkedIn account, cookies, private Voyager APIs, proxies, or browser automation.

The CLI follows the current public-search behavior documented by `curious_coder/linkedin-jobs-scraper`: search URLs take priority over structured fields, supported URL filters are preserved, discontinued filters are converted into natural-language keywords, results are deduplicated by job ID, and repeated pages terminate pagination.

## Install

```bash
uv sync
uv run linkedin-jobs --help
```

Install it as a standalone command:

```bash
uv tool install .
linkedin-jobs --help
```

## Search

Structured search:

```bash
uv run linkedin-jobs search "software engineer" \
  --location "London, United Kingdom" \
  --date-posted pastWeek \
  --workplace-type remote \
  --job-type full-time \
  --limit 25 \
  --format json
```

Use a public LinkedIn search URL:

```bash
uv run linkedin-jobs search \
  --url 'https://www.linkedin.com/jobs/search/?keywords=data%20engineer&location=London&f_TPR=r604800' \
  --limit 50 \
  --format jsonl
```

Multiple search URLs:

```bash
uv run linkedin-jobs search \
  --url 'https://www.linkedin.com/jobs/search/?keywords=backend%20engineer&location=London' \
  --url 'https://www.linkedin.com/jobs/search/?keywords=platform%20engineer&location=London' \
  --limit-per-source 25 \
  --global-limit 40 \
  --format csv \
  --output results/jobs.csv
```

Fast listing-only search:

```bash
uv run linkedin-jobs search "data engineer" --location Germany --no-details --format table
```

## Current search behavior

LinkedIn's 2026 public jobs search retains these URL filters:

- Keywords, location, geo ID, and distance.
- Date posted (`f_TPR`).
- Company IDs (`f_C`).
- Easy Apply (`f_AL`).
- Under ten applicants (`f_EA`).

Experience, job type, workplace type, remote, and date sorting are no longer reliable URL filters. With `--auto-convert` enabled, the CLI converts them into natural-language keywords. Use `--no-auto-convert` only when reproducing an old URL exactly.

## Output

Formats are `json`, `jsonl`, `csv`, and `table`. JSON field names mirror the reference Actor where public pages provide equivalent data, including `id`, `link`, `title`, `companyName`, `location`, `postedAt`, `descriptionHtml`, `descriptionText`, `applicantsCount`, `seniorityLevel`, `employmentType`, and `industries`.

Public LinkedIn pages do not consistently expose company enrichment, recruiter details, salary, workplace type, or the external application URL. Missing fields are returned as `null` or empty lists.

## Responsible operation

The default delay is one second between requests and each search defaults to 25 jobs. Keep rates conservative, do not use the tool to evade access controls, and ensure your use complies with LinkedIn's terms and applicable law.

See `docs/reverse-engineering-notes.md` for observed behavior, inferences, and remaining unknowns.


## Application integration extensions

`LinkedInPublicClient` now accepts `max_requests` (default 250), `checkpoint(jobs)` and `heartbeat()` hooks. `client.diagnostics` reports HTTP attempts, search pages, detail failures, cap reach and termination reasons. Request attempts include retries; Retry-After waits are capped at 30 seconds. Access challenges and unknown nonempty search markup raise `LinkedInClientError` instead of appearing to be healthy zero-result searches. Missing detail descriptions are recorded as detail errors. Checkpoints contain the complete accumulated bounded job list and are emitted after search pages and detail attempts. Exceptions from persistence/heartbeat callbacks propagate, stopping a run that loses ownership.

The job-market-intelligence sibling repository vendors this package and uses these hooks in its background worker. Existing CLI search output remains compatible; diagnostics are exposed through the Python API rather than changing the JSON array output.
