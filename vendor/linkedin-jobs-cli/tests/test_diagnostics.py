import httpx
import pytest
from linkedin_jobs_cli.client import LinkedInClientError, LinkedInPublicClient
from linkedin_jobs_cli.models import SearchSource


@pytest.mark.parametrize('markup', ['<html>unexpected</html>', '<input name="session_key">'])
def test_unknown_or_challenge_is_not_empty_success(markup):
    with LinkedInPublicClient(delay=0, transport=httpx.MockTransport(lambda _: httpx.Response(200, text=markup))) as client:
        with pytest.raises(LinkedInClientError):
            client.search([SearchSource('test', {})])


def test_request_limit_is_hard():
    with LinkedInPublicClient(delay=0, max_requests=1, transport=httpx.MockTransport(lambda _: httpx.Response(200, text=''))) as client:
        assert client.search([SearchSource('test', {})]) == []
        assert client.diagnostics.requests == 1
        with pytest.raises(LinkedInClientError, match='budget'):
            client.search([SearchSource('test', {})])


def test_missing_detail_description_is_counted():
    card = '''<div class="job-search-card" data-entity-urn="urn:li:jobPosting:123"><h3 class="base-search-card__title">Engineer</h3><h4 class="base-search-card__subtitle">Example</h4></div>'''
    def handler(request):
        return httpx.Response(200, text=card if 'seeMore' in request.url.path else '<html>No description</html>')
    checkpoints = []
    beats = []
    with LinkedInPublicClient(delay=0, checkpoint=lambda jobs: checkpoints.append(len(jobs)), heartbeat=lambda: beats.append(True), transport=httpx.MockTransport(handler)) as client:
        jobs = client.search([SearchSource('test', {})], limit_per_source=1)
        assert client.diagnostics.capped
        assert client.diagnostics.detail_failures == 1
        assert jobs[0].detail_error
        assert checkpoints and beats
