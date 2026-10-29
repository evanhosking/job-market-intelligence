from __future__ import annotations

import random
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Any

import httpx

from linkedin_jobs_cli.models import JobPosting, SearchSource
from linkedin_jobs_cli.parser import enrich_job_from_detail, parse_search_results


SEARCH_ENDPOINT = "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search"
DETAIL_ENDPOINT = "https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/{job_id}"
PAGE_SIZE = 10
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36"
)


class LinkedInClientError(RuntimeError):
    pass


@dataclass
class CollectionDiagnostics:
    requests: int = 0
    pages: int = 0
    detail_failures: int = 0
    capped: bool = False
    termination: list[str] = field(default_factory=list)


ProgressCallback = Callable[[str], None]


class LinkedInPublicClient:
    def __init__(
        self,
        *,
        timeout: float = 20,
        delay: float = 1,
        max_retries: int = 2,
        user_agent: str = DEFAULT_USER_AGENT,
        transport: httpx.BaseTransport | None = None,
        progress: ProgressCallback | None = None,
        max_requests: int = 250,
        checkpoint: Callable[[list[JobPosting]], None] | None = None,
        heartbeat: Callable[[], None] | None = None,
    ) -> None:
        if delay < 0:
            raise ValueError("Delay must be zero or greater")
        if max_requests < 1:
            raise ValueError("max_requests must be positive")
        self.max_requests = max_requests
        self.checkpoint = checkpoint or (lambda _: None)
        self.heartbeat = heartbeat or (lambda: None)
        self.diagnostics = CollectionDiagnostics()
        self.delay = delay
        self.max_retries = max_retries
        self.progress = progress or (lambda _: None)
        self._last_request_at: float | None = None
        self._client = httpx.Client(
            timeout=timeout,
            follow_redirects=True,
            transport=transport,
            headers={
                "Accept": "text/html,application/xhtml+xml",
                "Accept-Language": "en-GB,en;q=0.9",
                "Referer": "https://www.linkedin.com/jobs/search/",
                "User-Agent": user_agent,
            },
        )

    def __enter__(self) -> LinkedInPublicClient:
        return self

    def __exit__(self, *_: Any) -> None:
        self.close()

    def close(self) -> None:
        self._client.close()

    def _pause(self) -> None:
        if self.delay <= 0 or self._last_request_at is None:
            return
        target = self.delay * random.uniform(0.85, 1.15)
        remaining = target - (time.monotonic() - self._last_request_at)
        if remaining > 0:
            time.sleep(remaining)

    def _get(self, url: str, *, params: dict[str, str] | None = None) -> str:
        last_error: Exception | None = None
        for attempt in range(self.max_retries + 1):
            self.heartbeat()
            if self.diagnostics.requests >= self.max_requests:
                raise LinkedInClientError("Request budget exhausted")
            self._pause()
            self.diagnostics.requests += 1
            try:
                response = self._client.get(url, params=params)
                self._last_request_at = time.monotonic()
            except httpx.RequestError as error:
                self._last_request_at = time.monotonic()
                last_error = error
                if attempt == self.max_retries:
                    break
                time.sleep(min(2**attempt, 8))
                continue

            if response.status_code == 429:
                if attempt == self.max_retries:
                    raise LinkedInClientError("LinkedIn rate-limited the public search request (HTTP 429)")
                retry_after = response.headers.get("Retry-After", "")
                wait = min(float(retry_after), 30) if retry_after.isdigit() else min(2 ** (attempt + 1), 8)
                time.sleep(wait)
                continue
            if response.status_code >= 500:
                if attempt == self.max_retries:
                    raise LinkedInClientError(f"LinkedIn returned HTTP {response.status_code}")
                time.sleep(min(2 ** (attempt + 1), 8))
                continue
            if response.status_code == 404:
                raise LinkedInClientError("LinkedIn job posting was not found (HTTP 404)")
            if response.status_code >= 400:
                raise LinkedInClientError(f"LinkedIn returned HTTP {response.status_code}")
            if any(marker in str(response.url).lower() for marker in ("/authwall", "/checkpoint", "/login")):
                raise LinkedInClientError("LinkedIn redirected to an access challenge")
            text = response.text
            lowered = text.lower()
            if any(marker in lowered for marker in ('name="session_key"', 'id="authwall', '<title>security verification')):
                raise LinkedInClientError("LinkedIn returned an access challenge")
            return text

        raise LinkedInClientError(f"Could not reach LinkedIn: {last_error}")

    def search(
        self,
        sources: Iterable[SearchSource],
        *,
        limit_per_source: int = 25,
        global_limit: int | None = None,
        include_details: bool = True,
    ) -> list[JobPosting]:
        if limit_per_source < 1:
            raise ValueError("Limit per source must be at least 1")
        if global_limit is not None and global_limit < 1:
            raise ValueError("Global limit must be at least 1")

        collected: list[JobPosting] = []
        seen_job_ids: set[str] = set()

        for source in sources:
            source_count = 0
            offset = 0
            previous_signature: tuple[str, ...] | None = None

            while source_count < limit_per_source:
                if global_limit is not None and len(collected) >= global_limit:
                    break
                params = dict(source.params)
                params["start"] = str(offset)
                self.progress(f"Searching {source.label} at offset {offset}")
                html = self._get(SEARCH_ENDPOINT, params=params)
                self.diagnostics.pages += 1
                page = parse_search_results(html, source_url=source.original_url or source.label)
                if not page:
                    if html.strip() and not any(marker in html.lower() for marker in ("no-results", "no jobs found", "no matching jobs", "no results found")):
                        raise LinkedInClientError("Unrecognized nonempty search response; possible schema drift or challenge")
                    self.diagnostics.termination.append("empty_page")
                    break

                signature = tuple(job.id for job in page)
                if signature == previous_signature:
                    self.diagnostics.termination.append("repeated_page")
                    break
                previous_signature = signature

                new_jobs = [job for job in page if job.id not in seen_job_ids]
                if not new_jobs:
                    self.diagnostics.termination.append("no_new_ids")
                    break

                for job in new_jobs:
                    if source_count >= limit_per_source:
                        break
                    if global_limit is not None and len(collected) >= global_limit:
                        break
                    seen_job_ids.add(job.id)
                    collected.append(job)
                    source_count += 1

                self.checkpoint(collected)
                if len(page) < PAGE_SIZE:
                    break
                offset += PAGE_SIZE

            if source_count >= limit_per_source or (global_limit is not None and len(collected) >= global_limit):
                self.diagnostics.capped = True
            if global_limit is not None and len(collected) >= global_limit:
                break

        if include_details:
            for index, job in enumerate(collected, start=1):
                self.progress(f"Fetching details {index}/{len(collected)} for job {job.id}")
                try:
                    html = self._get(DETAIL_ENDPOINT.format(job_id=job.id))
                    enrich_job_from_detail(job, html)
                    if not job.description_text:
                        raise LinkedInClientError("Detail page has no description; possible schema drift")
                except LinkedInClientError as error:
                    job.detail_error = str(error)
                    self.diagnostics.detail_failures += 1
                self.checkpoint(collected)

        return collected
