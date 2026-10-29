from datetime import date, datetime, timedelta

import httpx
import pytest
from linkedin_jobs_cli.client import LinkedInClientError, LinkedInPublicClient
from linkedin_jobs_cli.models import SearchSource
from pydantic import ValidationError
from sqlalchemy import func, select

from market.analytics import compute, materialize
from market.config import Settings
from market.contracts import JobExtraction, MonitorConfig, Requirement
from market.extraction import clean_description, evidence_check, rules
from market.models import (
    Extraction,
    Metric,
    Observation,
    Quarantine,
    QueryVersion,
    Run,
    Snapshot,
    SourceJob,
)
from market.pipeline import enqueue, ingest, schedule, work_once


def imported(context, rows, clock):
    factory, settings, mid, qid = context
    with factory.begin() as db:
        run = enqueue(db, mid, settings, clock)
        ingest(db, run, rows, settings, clock)
        run.state = "completed"
        run.finished_at = clock.isoformat()
        materialize(db, qid)
        return run.id


def test_offline_end_to_end_and_replay(context, rows, clock, client):
    rid = imported(context, rows, clock)
    factory, settings, mid, qid = context
    with factory.begin() as db:
        ingest(db, db.get(Run, rid), rows + rows, settings, clock)
        materialize(db, qid)
        for model in (SourceJob, Observation, Snapshot, Extraction):
            assert db.scalar(select(func.count()).select_from(model)) == 2
        assert db.scalar(select(func.count()).select_from(Metric)) == 2
    response = client.get(f"/api/v1/monitors/{mid}/overview?from=2026-10-29&to=2026-10-29")
    assert response.status_code == 200
    assert response.json()["data"]["jobs"] == 2
    assert response.json()["meta"]["demo"] is True
    result = client.get(f"/api/v1/monitors/{mid}/jobs?limit=1").json()
    assert len(result["data"]) == 1 and result["next_cursor"]
    next_page = client.get(
        f"/api/v1/monitors/{mid}/jobs?limit=1&cursor={result['next_cursor']}"
    ).json()
    assert next_page["data"][0]["id"] != result["data"][0]["id"]
    assert "90071992547409931234" in [j["id"] for j in result["data"] + next_page["data"]]


def test_mobility_regression(rows):
    text = clean_description(rows[0])
    result = rules(rows[0], text)
    assert result.compensation == []
    assert result.seniority == "senior" and result.seniority_basis == "explicit_title"
    assert result.extraction_warnings
    assert result.work_arrangement == "remote"
    assert "UTC -1 to UTC +3" in result.remote_geo_restriction
    req = {r.canonical_candidate: r for r in result.requirements}
    assert (
        req["Azure"].alternatives_group
        == req["AWS"].alternatives_group
        == req["GCP"].alternatives_group
    )
    assert req["Kafka"].alternatives_group == req["RabbitMQ"].alternatives_group
    assert req["Claude Code"].requiredness == "preferred"
    assert req["Kubernetes"].requiredness == "familiarity"
    assert ".NET" in req and "C#" in req
    assert result.minimum_experience_years is None
    assert result.degree_requirement == "not_stated"


@pytest.mark.parametrize(
    "salary,currency,period,lower,upper",
    [
        ("£50k–£70k/year", "GBP", "year", 50000, 70000),
        ("$60/hour", None, "hour", 60, 60),
        ("€4k/month", "EUR", "month", 4000, 4000),
        ("competitive", None, None, None, None),
        ("£70k–£50k/year", None, None, None, None),
        ("up to £70k/year", "GBP", "year", None, 70000),
    ],
)
def test_salary(salary, currency, period, lower, upper):
    raw = {"salary": salary, "descriptionText": "Engineering job", "title": "Engineer"}
    result = rules(raw, raw["descriptionText"])
    if period is None:
        assert not result.compensation
    else:
        pay = result.compensation[0]
        assert (pay.currency_iso, pay.period, pay.min_amount, pay.max_amount) == (
            currency,
            period,
            lower,
            upper,
        )


def test_schema_and_fabricated_evidence():
    with pytest.raises(ValidationError):
        JobExtraction(seniority="galactic")
    result = JobExtraction(
        requirements=[
            Requirement(
                canonical_candidate="Python",
                surface_text="Python",
                category="language",
                requiredness="required",
                context="candidate_requirement",
                evidence_quote="invented",
                confidence=0.9,
            )
        ]
    )
    with pytest.raises(ValueError, match="evidence"):
        evidence_check(result, "Required Python")
    assert (
        clean_description({"descriptionHtml": "<p>Python &amp; SQL</p><script>alert(1)</script>"})
        == "Python & SQL"
    )
    assert (
        rules(
            {"title": "Engineer"}, "5 years in business. No degree required."
        ).minimum_experience_years
        is None
    )


def test_versions_and_refresh(client, context):
    _, _, mid, _ = context
    original = client.get(f"/api/v1/monitors/{mid}").json()
    config = MonitorConfig(name="New name", location="Paris").model_dump()
    assert client.patch(f"/api/v1/monitors/{mid}", json=config).json()["version"] == 2
    with context[0]() as db:
        old = db.scalar(select(QueryVersion).where(QueryVersion.id == original["query_version_id"]))
        assert old.config["location"] == "United Kingdom"
    a = client.post(f"/api/v1/monitors/{mid}/refresh")
    b = client.post(f"/api/v1/monitors/{mid}/refresh")
    assert a.status_code == 202 and a.json()["run_id"] == b.json()["run_id"]
    assert (
        client.post(
            f"/api/v1/monitors/{mid}/refresh", headers={"Origin": "https://evil.example"}
        ).status_code
        == 403
    )
    assert client.get(f"/api/v1/monitors/{mid}/jobs?limit=999").status_code == 422


def test_failed_is_missing_and_demo_isolated(context, rows, clock):
    factory, settings, mid, qid = context
    with factory.begin() as db:
        run = enqueue(db, mid, settings, clock)
        ingest(db, run, rows, settings, clock)
        run.state = "failed"
        result = compute(db, qid, date(2026, 10, 29), date(2026, 10, 29))
        assert result["overview"]["jobs"] == 0
        assert result["overview"]["daily"][0]["newly_observed"] is None
        run.state = "completed"
        result = compute(db, qid, date(2026, 10, 29), date(2026, 10, 29), mode="live")
        assert result["overview"]["jobs"] == 0


def test_changed_and_unchanged_content(context, rows, clock):
    imported(context, rows, clock)
    factory, settings, mid, _ = context
    with factory.begin() as db:
        run = enqueue(db, mid, settings, clock + timedelta(days=1))
        row = {**rows[0], "trackingId": "changed", "applicantsCount": "999"}
        ingest(db, run, [row], settings, clock + timedelta(days=1))
        assert db.scalar(select(func.count()).select_from(Snapshot)) == 2
        run.state = "completed"
    with factory.begin() as db:
        run = enqueue(db, mid, settings, clock + timedelta(days=2))
        row = {**rows[0], "descriptionText": "Required Python. Fully remote within UK."}
        ingest(db, run, [row], settings, clock + timedelta(days=2))
        assert db.scalar(select(func.count()).select_from(Snapshot)) == 3


def test_quarantine_and_budget(context, clock):
    factory, settings, mid, _ = context
    with factory.begin() as db:
        run = enqueue(db, mid, settings, clock)
        ingest(db, run, [{"id": 1, "title": "X"}, {"title": "bad"}], settings, clock)
        assert db.scalar(select(func.count()).select_from(Quarantine)) == 2
        run.state = "failed"
    settings.monthly_item_cap = 25
    with factory.begin() as db, pytest.raises(ValueError, match="budget"):
        enqueue(db, mid, settings, clock + timedelta(days=1))


@pytest.mark.parametrize("stamp", ["2026-03-29T07:00:00+00:00", "2026-10-25T07:00:00+00:00"])
def test_dst_schedule_idempotent(context, stamp):
    factory, settings, _, _ = context
    with factory.begin() as db:
        clock = datetime.fromisoformat(stamp)
        schedule(db, settings, clock)
        schedule(db, settings, clock)
        assert db.scalar(select(func.count()).select_from(Run)) == 1


def test_worker_offline_and_expired_lease(context, clock):
    factory, settings, mid, _ = context
    with factory.begin() as db:
        rid = enqueue(db, mid, settings, clock).id
    assert work_once(factory, settings, clock)
    with factory.begin() as db:
        assert db.get(Run, rid).state == "completed"
        run = enqueue(db, mid, settings, clock + timedelta(days=1))
        run.state = "running"
        run.lease_until = clock.isoformat()
        interrupted = run.id
    work_once(factory, settings, clock + timedelta(days=2))
    with factory() as db:
        assert db.get(Run, interrupted).state == "interrupted"


def test_cli_challenge_and_request_cap():
    for content in ["<html>unknown markup</html>", '<input name="session_key">']:
        with (
            LinkedInPublicClient(
                delay=0, transport=httpx.MockTransport(lambda _: httpx.Response(200, text=content))
            ) as cli,
            pytest.raises(LinkedInClientError),
        ):
            cli.search([SearchSource("test", {})])
    with LinkedInPublicClient(
        delay=0,
        max_requests=1,
        transport=httpx.MockTransport(lambda _: httpx.Response(200, text="")),
    ) as cli:
        cli.search([SearchSource("test", {})])
        with pytest.raises(LinkedInClientError, match="budget"):
            cli.search([SearchSource("test", {})])


def test_deployed_auth(context):
    from fastapi.testclient import TestClient

    from market.api import create_app

    settings = Settings(_env_file=None, app_mode="deployed", admin_key="x" * 32)
    client = TestClient(create_app(context[0], settings))
    assert client.get("/api/v1/monitors").status_code == 401
    assert client.get("/api/v1/monitors", headers={"X-Admin-Key": "x" * 32}).status_code == 200


def test_offline_never_invokes_configured_live_model(context, rows, clock, monkeypatch):
    from market import extraction

    def forbidden(*args, **kwargs):
        raise AssertionError("Paid request attempted during offline replay")

    monkeypatch.setattr(extraction.httpx.Client, "post", forbidden)
    factory, settings, mid, _ = context
    settings.llm_mode = "live"
    with factory.begin() as db:
        run = enqueue(db, mid, settings, clock)
        result = ingest(db, run, rows, settings, clock)
        assert result["extracted"] == 2


def test_live_budget_and_demo_do_not_collide(context, clock):
    factory, settings, mid, _ = context
    with factory.begin() as db:
        offline = enqueue(db, mid, settings, clock)
        offline.state = "completed"
        db.flush()
        settings.collection_mode = "live"
        settings.llm_mode = "live"
        settings.llm_monthly_request_cap = 0
        # Collection remains available even when the independently reserved model budget is zero.
        live = enqueue(db, mid, settings, clock)
        assert live.id != offline.id and live.mode == "live"


def test_date_window_validation(client, context):
    mid = context[2]
    assert client.get(f"/api/v1/monitors/{mid}/overview?from=1900-01-01").status_code == 422
    assert (
        client.get(f"/api/v1/monitors/{mid}/overview?from=2026-10-29&to=2026-10-01").status_code
        == 422
    )


def test_partial_live_jobs_visible_without_contaminating_trends(context, rows, clock, client):
    factory, settings, mid, qid = context
    settings.collection_mode = "live"
    partial_rows = [
        rows[0],
        {**rows[1], "descriptionText": "", "detailError": "LinkedIn returned an access challenge"},
    ]
    with factory.begin() as db:
        run = enqueue(db, mid, settings, clock)
        ingest(db, run, partial_rows, settings, clock)
        run.state = "partial"
        run.finished_at = clock.isoformat()
    window = "?from=2026-10-29&to=2026-10-29"
    jobs = client.get(f"/api/v1/monitors/{mid}/jobs{window}").json()
    assert len(jobs["data"]) == 2
    assert {j["collection_state"] for j in jobs["data"]} == {"partial"}
    assert sum(j["description_available"] for j in jobs["data"]) == 1
    assert jobs["meta"]["includes_partial"]
    overview = client.get(f"/api/v1/monitors/{mid}/overview{window}").json()
    assert overview["data"]["jobs"] == 0
    assert overview["data"]["daily"][0]["newly_observed"] is None
    assert overview["meta"]["collected_jobs"] == 2
    assert overview["meta"]["collected_parsed"] == 1
    assert overview["meta"]["partial_only_jobs"] == 2
    assert overview["meta"]["latest_collection_at"] == clock.isoformat()
    settings.collection_mode = "offline"
    assert client.get(f"/api/v1/monitors/{mid}/jobs{window}").json()["data"] == []
