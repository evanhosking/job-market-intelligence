from __future__ import annotations

import hashlib
import random
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import urljoin, urlsplit

import httpx
from bs4 import BeautifulSoup

from linkedin_jobs_cli.models import JobPosting, SearchSource
from linkedin_jobs_cli.parser import (
    CARD_SELECTOR,
    enrich_job_from_detail,
    job_description,
    parse_search_results,
)

SEARCH_ENDPOINT = "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search"
DETAIL_ENDPOINT = "https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/{job_id}"
PAGE_SIZE = 10
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36"
)


class LinkedInClientError(RuntimeError):
    def __init__(self, message, *, code="integration_error", retry_at=None):
        super().__init__(message)
        self.code = code
        self.retry_at = retry_at


def retry_time(value, clock=None):
    clock = clock or datetime.now(UTC)
    try:
        target = (
            clock + timedelta(seconds=int(value))
            if value.strip().isdigit()
            else parsedate_to_datetime(value)
        )
        return max(clock, target.astimezone(UTC))
    except (ValueError, TypeError, OverflowError):
        return clock + timedelta(seconds=60)


@dataclass
class CollectionDiagnostics:
    requests: int = 0
    pages: int = 0
    detail_failures: int = 0
    capped: bool = False
    termination: list[str] = field(default_factory=list)
    listing_status: str = "pending"
    enrichment_status: str = "pending"
    access_paused: bool = False
    responses: list[dict] = field(default_factory=list)


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
            follow_redirects=False,
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
        target = self.delay * random.uniform(1, 1.15)
        remaining = target - (time.monotonic() - self._last_request_at)
        if remaining > 0:
            time.sleep(remaining)

    def _get(self, url: str, *, params: dict[str, str] | None = None) -> str:
        for attempt in range(self.max_retries + 1):
            current_url = url
            for redirect in range(6):
                self.heartbeat()
                if self.diagnostics.requests >= self.max_requests:
                    raise LinkedInClientError("Request budget exhausted", code="budget_exhausted")
                self._pause()
                self.diagnostics.requests += 1
                try:
                    response = self._client.get(
                        current_url, params=params if redirect == 0 else None
                    )
                except httpx.RequestError:
                    self._last_request_at = time.monotonic()
                    if attempt == self.max_retries:
                        raise LinkedInClientError(
                            "Network request failed", code="transient"
                        ) from None
                    time.sleep(min(2**attempt, 8))
                    break
                self._last_request_at = time.monotonic()
                path = urlsplit(str(response.url)).path
                soup = BeautifulSoup(response.text, "html.parser")
                requested_id = url.rsplit("/", 1)[-1] if "/jobPosting/" in url else None
                has_description = bool(job_description(soup, requested_id))
                has_cards = bool(soup.select_one(CARD_SELECTOR))
                diagnostic = {
                    "status": response.status_code,
                    "path": path[:200],
                    "bytes": len(response.content),
                    "sha256": hashlib.sha256(response.content).hexdigest(),
                    "has_description": has_description,
                    "has_cards": has_cards,
                }
                self.diagnostics.responses = (self.diagnostics.responses + [diagnostic])[-30:]
                if response.is_redirect:
                    destination = urljoin(str(response.url), response.headers.get("location", ""))
                    parsed = urlsplit(destination)
                    if any(
                        marker in parsed.path.lower()
                        for marker in ("/authwall", "/checkpoint", "/login", "/uas/")
                    ):
                        raise LinkedInClientError(
                            "LinkedIn redirected to an access challenge", code="access_challenge"
                        )
                    if (
                        parsed.scheme != "https"
                        or parsed.hostname not in {"linkedin.com", "www.linkedin.com"}
                        or redirect == 5
                    ):
                        raise LinkedInClientError("Unsupported redirect", code="redirect_error")
                    current_url = destination
                    continue
                if response.status_code == 429:
                    target = retry_time(response.headers.get("Retry-After", ""))
                    raise LinkedInClientError(
                        "LinkedIn rate limited this request",
                        code="rate_limited",
                        retry_at=target.isoformat(),
                    )
                if response.status_code in {401, 403, 999}:
                    raise LinkedInClientError(
                        "LinkedIn denied guest access", code="access_challenge"
                    )
                if response.status_code >= 500:
                    if attempt == self.max_retries:
                        raise LinkedInClientError(
                            "LinkedIn temporary server error", code="transient"
                        )
                    time.sleep(min(2 ** (attempt + 1), 8))
                    break
                if response.status_code == 404:
                    raise LinkedInClientError(
                        "LinkedIn job posting was not found", code="not_found"
                    )
                if response.status_code >= 400:
                    raise LinkedInClientError("LinkedIn rejected the request", code="http_error")
                lowered = response.text.lower()
                challenge = (
                    soup.select_one('input[name="session_key"], #authwall')
                    or "<title>security verification" in lowered
                )
                # A sign-in widget does not invalidate a genuine public job response.
                if challenge and not has_description and not has_cards:
                    raise LinkedInClientError(
                        "LinkedIn returned an access challenge", code="access_challenge"
                    )
                return response.text
        raise LinkedInClientError("Transient request failure", code="transient")

    def fetch_detail(self, job: JobPosting) -> JobPosting:
        try:
            html = self._get(DETAIL_ENDPOINT.format(job_id=job.id))
            enrich_job_from_detail(job, html)
            if not job.description_text:
                raise LinkedInClientError("Detail page has no description", code="schema_drift")
        except LinkedInClientError as error:
            job.detail_scraped = False
            job.detail_error = error.code
            self.diagnostics.detail_failures += 1
            raise
        return job

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
                    if html.strip() and not any(
                        marker in html.lower()
                        for marker in (
                            "no-results",
                            "no jobs found",
                            "no matching jobs",
                            "no results found",
                        )
                    ):
                        raise LinkedInClientError(
                            "Unrecognized nonempty search response; possible schema drift or challenge"
                        )
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
                if len(BeautifulSoup(html, "html.parser").select(CARD_SELECTOR)) < PAGE_SIZE:
                    break
                offset += PAGE_SIZE

            if source_count >= limit_per_source or (
                global_limit is not None and len(collected) >= global_limit
            ):
                self.diagnostics.capped = True
            if global_limit is not None and len(collected) >= global_limit:
                break

        self.diagnostics.listing_status = "completed"
        self.diagnostics.enrichment_status = "not_requested"
        if include_details:
            self.diagnostics.enrichment_status = "completed"
            for job in collected:
                try:
                    self.fetch_detail(job)
                except LinkedInClientError as error:
                    self.diagnostics.enrichment_status = "partial"
                    if error.code in {"access_challenge", "rate_limited", "budget_exhausted"}:
                        self.diagnostics.access_paused = error.code == "access_challenge"
                        self.checkpoint(collected)
                        break
                self.checkpoint(collected)
        return collected
