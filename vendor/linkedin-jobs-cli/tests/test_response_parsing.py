import json

import httpx
import pytest
from linkedin_jobs_cli.client import LinkedInClientError, LinkedInPublicClient
from linkedin_jobs_cli.models import JobPosting, SearchSource
from linkedin_jobs_cli.parser import enrich_job_from_detail, parse_search_results


def job():
    return JobPosting("123", "https://www.linkedin.com/jobs/view/123", "Engineer", "Example", "UK")


def structured(job_id="123", description="<p>Requirements</p><p>Build <b>Python</b> APIs.</p>"):
    return (
        '<script type="application/ld+json">'
        + json.dumps(
            {
                "@graph": [
                    {
                        "@type": "JobPosting",
                        "identifier": {"value": job_id},
                        "description": description,
                    }
                ]
            }
        )
        + "</script>"
    )


def test_search_variants_and_missing_company_are_preserved():
    html = """<div class="base-search-card" data-job-id="90071992547409931234">
      <a href="https://www.linkedin.com/jobs/view/90071992547409931234"><h3>Engineer</h3></a>
    </div>"""
    jobs = parse_search_results(html)
    assert len(jobs) == 1
    assert jobs[0].id == "90071992547409931234"
    assert jobs[0].company_name == "Unknown"
    assert jobs[0].title == "Engineer"


@pytest.mark.parametrize(
    "wrapper",
    [
        '<div id="job-details">{}</div>',
        '<div itemprop="description">{}</div>',
        '<div class="description__text">{}</div>',
        "{}",
    ],
)
def test_description_variants_and_inline_text(wrapper):
    content = (
        structured() if wrapper == "{}" else "<p>Requirements</p><p>Build <b>Python</b> APIs.</p>"
    )
    html = '<input name="session_key">' + wrapper.format(content)
    with LinkedInPublicClient(
        delay=0, transport=httpx.MockTransport(lambda _: httpx.Response(200, text=html))
    ) as client:
        result = client.fetch_detail(job())
        assert result.description_text == "Requirements\nBuild Python APIs."
        assert client.diagnostics.responses[0]["has_description"]


def test_structured_data_must_match_job_identity():
    result = enrich_job_from_detail(job(), structured("999"))
    assert not result.description_text
    assert not result.detail_scraped
    html = '<input name="session_key">' + structured("999")
    with LinkedInPublicClient(
        delay=0, transport=httpx.MockTransport(lambda _: httpx.Response(200, text=html))
    ) as client:
        with pytest.raises(LinkedInClientError) as caught:
            client.fetch_detail(job())
        assert caught.value.code == "access_challenge"


def test_empty_markup_falls_back_to_matching_structured_data():
    result = enrich_job_from_detail(
        job(), '<div class="show-more-less-html__markup"> </div>' + structured()
    )
    assert result.description_text == "Requirements\nBuild Python APIs."


def test_invalid_json_and_scripts_do_not_become_descriptions():
    result = enrich_job_from_detail(job(), '<script type="application/ld+json">bad json</script>')
    assert not result.description_text
    result = enrich_job_from_detail(
        job(), '<div id="job-details"><script>secret()</script><p>Build Python.</p></div>'
    )
    assert result.description_text == "Build Python."


def test_duplicate_cards_do_not_truncate_pagination():
    def card(n):
        return f'<div class="job-search-card" data-entity-urn="urn:li:jobPosting:{n}"><h3 class="base-search-card__title">Engineer</h3></div>'

    offsets = []

    def handler(request):
        offset = request.url.params["start"]
        offsets.append(offset)
        return httpx.Response(
            200, text="".join(card(i) for i in range(9)) + card(0) if offset == "0" else card(10)
        )

    with LinkedInPublicClient(delay=0, transport=httpx.MockTransport(handler)) as client:
        jobs = client.search([SearchSource("test", {})], include_details=False)
    assert len(jobs) == 10
    assert offsets == ["0", "10"]
