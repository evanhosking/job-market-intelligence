from __future__ import annotations

import httpx

from linkedin_jobs_cli.client import LinkedInPublicClient
from linkedin_jobs_cli.models import SearchSource


def _card(job_id: str) -> str:
    return f"""
    <div class="base-search-card job-search-card" data-entity-urn="urn:li:jobPosting:{job_id}">
      <a class="base-card__full-link" href="https://www.linkedin.com/jobs/view/example-{job_id}"></a>
      <h3 class="base-search-card__title">Engineer {job_id}</h3>
      <h4 class="base-search-card__subtitle"><a href="https://www.linkedin.com/company/example">Example</a></h4>
      <span class="job-search-card__location">London</span>
    </div>
    """


def test_client_stops_when_linkedin_repeats_a_page() -> None:
    page = "".join(_card(str(1000 + index)) for index in range(10))
    offsets: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        offsets.append(request.url.params.get("start", ""))
        return httpx.Response(200, text=page)

    with LinkedInPublicClient(delay=0, transport=httpx.MockTransport(handler)) as client:
        jobs = client.search(
            [SearchSource(label="test", params={"keywords": "engineer"})],
            limit_per_source=25,
            include_details=False,
        )

    assert len(jobs) == 10
    assert offsets == ["0", "10"]
