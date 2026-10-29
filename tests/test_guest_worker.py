from datetime import UTC, datetime, timedelta

import httpx
import pytest
from linkedin_jobs_cli.client import LinkedInClientError, LinkedInPublicClient, retry_time
from linkedin_jobs_cli.models import JobPosting
from sqlalchemy import func, select

from market.analytics import compute
from market.enrichment import enrich_jobs, resume_source, source_health
from market.models import DetailTask, Observation, Run
from market.pipeline import enqueue, ingest, work_once


def posting(job_id="123"):
    return JobPosting(
        id=job_id,
        link="https://www.linkedin.com/jobs/view/" + job_id,
        title="Engineer",
        company_name="Example",
        location="UK",
    )


def test_signin_widget_does_not_hide_public_description():
    html = (
        '<input name="session_key"><div class="show-more-less-html__markup">Required Python.</div>'
    )
    with LinkedInPublicClient(
        delay=0, transport=httpx.MockTransport(lambda _: httpx.Response(200, text=html))
    ) as client:
        job = client.fetch_detail(posting())
        assert job.description_text == "Required Python."
        assert client.diagnostics.responses[0]["has_description"]
        assert set(client.diagnostics.responses[0]) == {
            "status",
            "path",
            "bytes",
            "sha256",
            "has_description",
            "has_cards",
        }


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(403),
        httpx.Response(200, text='<input name="session_key">'),
        httpx.Response(302, headers={"Location": "https://www.linkedin.com/checkpoint/test"}),
    ],
)
def test_confirmed_challenges(response):
    with LinkedInPublicClient(delay=0, transport=httpx.MockTransport(lambda _: response)) as client:
        with pytest.raises(LinkedInClientError) as caught:
            client.fetch_detail(posting())
        assert caught.value.code == "access_challenge"
        assert client.diagnostics.requests == 1


def test_retry_after_preserved_without_early_retry():
    clock = datetime(2026, 10, 29, tzinfo=UTC)
    assert retry_time("120", clock) == clock + timedelta(seconds=120)
    assert retry_time("Fri, 30 Oct 2026 00:00:00 GMT", clock) == clock + timedelta(days=1)
    with LinkedInPublicClient(
        delay=0,
        transport=httpx.MockTransport(
            lambda _: httpx.Response(429, headers={"Retry-After": "120"})
        ),
    ) as client:
        with pytest.raises(LinkedInClientError) as caught:
            client.fetch_detail(posting())
        assert caught.value.code == "rate_limited"
        assert datetime.fromisoformat(caught.value.retry_at) > datetime.now(UTC) + timedelta(
            seconds=110
        )
        assert client.diagnostics.requests == 1


def test_redirects_count_against_request_cap():
    with LinkedInPublicClient(
        delay=0,
        max_requests=1,
        transport=httpx.MockTransport(
            lambda _: httpx.Response(
                302, headers={"location": "https://www.linkedin.com/jobs/view/123"}
            )
        ),
    ) as client:
        with pytest.raises(LinkedInClientError) as caught:
            client.fetch_detail(posting())
        assert caught.value.code == "budget_exhausted"


def test_offline_worker_never_claims_live_run(context, clock):
    factory, settings, mid, _ = context
    with factory.begin() as db:
        rid = enqueue(db, mid, settings.model_copy(update={"collection_mode": "live"}), clock).id
    assert not work_once(factory, settings, clock)
    with factory() as db:
        assert db.get(Run, rid).state == "queued"
    with pytest.raises(ValueError, match="authorization"):
        work_once(factory, settings.model_copy(update={"collection_mode": "live"}), clock)


def test_challenge_pauses_source_and_stops_remaining_detail_calls(context, clock):
    factory, settings, mid, _ = context
    checkpoints = []
    with LinkedInPublicClient(
        delay=0, transport=httpx.MockTransport(lambda _: httpx.Response(403))
    ) as client:
        rows = enrich_jobs(
            factory,
            client,
            [posting("1"), posting("2")],
            settings,
            lambda rows: checkpoints.append(list(rows)),
        )
        assert client.diagnostics.requests == 1
    assert len(rows) == 2
    assert rows[0]["detailError"] == "access_challenge"
    assert rows[1]["detailError"] == "source_access_unavailable"
    with factory.begin() as db:
        assert source_health(db).state == "paused"
        with pytest.raises(ValueError, match="unavailable"):
            enqueue(db, mid, settings.model_copy(update={"collection_mode": "live"}), clock)
        resume_source(db)
        assert source_health(db).state == "available"
        assert db.get(DetailTask, "1").state == "pending"


def test_successful_details_are_cached_across_runs(context):
    factory, settings, _, _ = context
    html = '<div class="show-more-less-html__markup">Required Python.</div>'
    with LinkedInPublicClient(
        delay=0, transport=httpx.MockTransport(lambda _: httpx.Response(200, text=html))
    ) as client:
        first = enrich_jobs(factory, client, [posting()], settings, lambda _: None)
        second = enrich_jobs(factory, client, [posting()], settings, lambda _: None)
        assert client.diagnostics.requests == 1
        assert second[0]["descriptionCached"] is True
        assert second[0]["descriptionFetchedAt"] == first[0]["descriptionFetchedAt"]


def test_stale_description_survives_failed_refresh(context):
    factory, settings, _, _ = context
    with factory.begin() as db:
        db.add(
            DetailTask(
                source_job_id="123",
                state="ready",
                attempts=0,
                payload={"descriptionText": "Required Python."},
                fetched_at=(datetime.now(UTC) - timedelta(days=8)).isoformat(),
            )
        )
    with LinkedInPublicClient(
        delay=0, transport=httpx.MockTransport(lambda _: httpx.Response(403))
    ) as client:
        rows = enrich_jobs(factory, client, [posting()], settings, lambda _: None)
    assert rows[0]["descriptionText"] == "Required Python."
    assert rows[0]["descriptionStale"] is True
    assert rows[0]["detailError"] == "access_challenge"


def test_completed_listing_stage_is_eligible_with_partial_descriptions(context, rows, clock):
    factory, settings, mid, qid = context
    rows = [rows[0], {**rows[1], "descriptionText": ""}]
    with factory.begin() as db:
        run = enqueue(db, mid, settings, clock)
        ingest(db, run, rows, settings, clock)
        run.state = "partial"
        run.diagnostics = {
            "listing_status": "completed",
            "enrichment_status": "partial",
            "capped": True,
        }
        result = compute(db, qid, clock.date(), clock.date())
        assert result["overview"]["jobs"] == 2
        assert result["meta"]["parsed"] == 1
        assert result["meta"]["description_coverage"] == 0.5
        assert result["overview"]["daily"][0]["newly_observed"] == 2
        # Successful later enrichment upgrades the observed snapshot without duplicate sightings.
        ingest(db, run, [{**rows[1], "descriptionText": "Required Python."}], settings, clock)
        assert db.scalar(select(func.count()).select_from(Observation)) == 2
        result = compute(db, qid, clock.date(), clock.date())
        assert result["meta"]["parsed"] == 2


def test_listing_failure_keeps_committed_checkpoint(context, rows, clock, monkeypatch):
    from market import pipeline

    factory, settings, mid, _ = context
    settings.collection_mode = "live"
    with factory.begin() as db:
        rid = enqueue(db, mid, settings, clock).id

    class FailingClient:
        def __init__(self, **kwargs):
            self.checkpoint = kwargs["checkpoint"]
            from linkedin_jobs_cli.client import CollectionDiagnostics

            self.diagnostics = CollectionDiagnostics(requests=2, pages=1)

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def search(self, *args, **kwargs):
            job = posting(rows[0]["id"])
            self.checkpoint([job])
            raise LinkedInClientError(
                "limited",
                code="rate_limited",
                retry_at=(datetime.now(UTC) + timedelta(minutes=2)).isoformat(),
            )

    monkeypatch.setattr(pipeline, "LinkedInPublicClient", FailingClient)
    work_once(factory, settings, clock, allow_live=True)
    with factory() as db:
        run = db.get(Run, rid)
        assert run.state == "partial"
        assert run.diagnostics["listing_status"] == "incomplete"
        assert db.scalar(select(func.count()).select_from(Observation)) == 1
        assert run.error == "rate_limited"


def test_common_requirement_wording():
    from market.extraction import rules

    for text in ("We require Python and PostgreSQL.", "Requirements: AWS or Azure."):
        result = rules({"title": "Engineer"}, text)
        assert result.requirements
        assert all(
            r.context == "candidate_requirement" and r.requiredness == "required"
            for r in result.requirements
        )


def test_due_details_resume_without_repeating_search(context, clock, monkeypatch):
    from linkedin_jobs_cli.client import CollectionDiagnostics

    from market import pipeline

    factory, settings, mid, _ = context
    settings.collection_mode = "live"
    raw = {
        "id": "123",
        "title": "Engineer",
        "companyName": "Example",
        "location": "UK",
        "descriptionText": "",
    }
    with factory.begin() as db:
        run = enqueue(db, mid, settings, clock)
        run.raw_checkpoint = [raw]
        run.state = "partial"
        run.diagnostics = {
            "listing_status": "completed",
            "enrichment_status": "partial",
            "capped": True,
        }
        ingest(db, run, [raw], settings, clock)
        db.add(DetailTask(source_job_id="123", state="pending", attempts=0, payload={}))

    class Client:
        def __init__(self, **kwargs):
            self.diagnostics = CollectionDiagnostics()

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def search(self, *args, **kwargs):
            raise AssertionError("Search must not repeat")

        def fetch_detail(self, job):
            job.description_text = "We require Python."
            return job

    monkeypatch.setattr(pipeline, "LinkedInPublicClient", Client)
    assert work_once(factory, settings, clock, allow_live=True)
    with factory() as db:
        latest = db.scalar(select(Run).where(Run.kind.like("manual-%")))
        assert latest.state == "completed"
        assert latest.input["resume_rows"] == [raw]
        assert latest.raw_checkpoint[0]["descriptionText"] == "We require Python."
