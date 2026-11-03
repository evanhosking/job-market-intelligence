from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import select

from market import analytics
from market.analytics import compute, materialize
from market.models import Extraction, Metric, Observation, QueryVersion, Run, Snapshot
from market.pipeline import enqueue, ingest


@pytest.fixture(autouse=True)
def fixed_today(monkeypatch):
    class FixedDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 11, 3, 12, tzinfo=UTC).astimezone(tz)

    monkeypatch.setattr(analytics, "datetime", FixedDateTime)


@pytest.fixture
def two_weeks(context):
    factory, settings, mid, _ = context
    with factory.begin() as db:
        for day in range(14):
            clock = datetime(2026, 10, 19, 12, tzinfo=UTC) + timedelta(days=day)
            run = enqueue(db, mid, settings, clock)
            rows = [
                {
                    "id": f"{day}-{i}",
                    "title": "Engineer",
                    "companyName": "Example",
                    "descriptionText": "Requirements\nExperience with Python."
                    + (" Experience with LLMs and API design." if day >= 7 and i < 3 else ""),
                }
                for i in range(5 if day < 7 else 6)
            ]
            ingest(db, run, rows, settings, clock)
            run.state = "completed"
            run.finished_at = clock.isoformat()
    return context


def weekly(context, **kwargs):
    factory, _, _, qid = context
    with factory() as db:
        return compute(db, qid, date(2026, 10, 7), date(2026, 11, 3), **kwargs)


def test_complete_weeks_and_evidence(two_weeks, client):
    result = weekly(two_weeks)
    comparison = result["overview"]["comparison"]
    assert comparison["status"] == comparison["prevalence_status"] == "ready"
    assert comparison["previous"]["from"] == "2026-10-19"
    assert comparison["previous"]["to"] == "2026-10-25"  # BST ends this Sunday.
    assert comparison["current"]["from"] == "2026-10-26"
    assert comparison["current"]["to"] == "2026-11-01"
    assert comparison["previous"]["eligible"] == 35
    assert comparison["current"]["eligible"] == 42
    assert comparison["volume"] == {"change": 7, "relative_change": 0.2}
    ai = next(c for c in comparison["changes"] if c.get("theme") == "ai_engineering")
    assert ai["previous_count"] == 0 and ai["current_count"] == 21
    assert ai["percentage_point_change"] == 50
    assert len(result["history"]) == 4
    assert result["history"][0]["complete"] is False
    assert result["history"][0]["coverage"] is None
    findings = result["overview"]["findings"]
    assert len(findings) == 4
    assert all(f["status"] == "tentative" for f in findings)
    factory, _, mid, _ = two_weeks
    with factory() as db:
        for finding in findings:
            for example in finding["examples"]:
                snapshot = db.get(Snapshot, example["snapshot_id"])
                assert example["quote"] in snapshot.description
                assert example["job_id"] in finding["job_ids"]
        materialize(db, two_weeks[3])
        db.flush()
        saved = db.scalar(select(Metric).where(Metric.period.like("%offline")))
        assert saved.result["overview"]["comparison"]["status"] == "ready"
    url = f"/api/v1/monitors/{mid}"
    overview = client.get(f"{url}/overview?from=2026-10-07&to=2026-11-03").json()
    insights = client.get(f"{url}/insights?from=2026-10-07&to=2026-11-03").json()
    assert overview["history"][-1]["parsed"] == 42
    assert overview["data"]["findings"] == insights["data"][:4]
    skills = client.get(f"{url}/skills?from=2026-10-07&to=2026-11-03&theme=ai_engineering").json()
    assert all(s["theme"] == "ai_engineering" for w in skills["history"] for s in w["skills"])


@pytest.mark.parametrize("state", ["failed", "partial", "queued"])
def test_missing_days_are_gaps_and_suppress_changes(two_weeks, state):
    factory, _, _, _ = two_weeks
    with factory.begin() as db:
        run = db.scalar(select(Run).where(Run.logical_date == "2026-10-28"))
        run.state = state
    result = weekly(two_weeks)
    comparison = result["overview"]["comparison"]
    assert comparison["status"] == comparison["prevalence_status"] == "insufficient_data"
    assert comparison["current"]["missing_days"] == ["2026-10-28"]
    assert comparison["volume"]["change"] is None
    assert all(c["percentage_point_change"] is None for c in comparison["changes"])
    assert not any(f["status"] == "tentative" for f in result["overview"]["findings"])


@pytest.mark.parametrize("problem", ["capped", "version", "mixed", "coverage", "stale"])
def test_quality_guards(two_weeks, problem):
    factory, _, _, _ = two_weeks
    with factory.begin() as db:
        run = db.scalar(select(Run).where(Run.logical_date == "2026-10-28"))
        if problem == "capped":
            run.diagnostics = {"capped": True}
        elif problem in {"version", "mixed"}:
            extractions = list(
                db.scalars(
                    select(Extraction)
                    .join(Snapshot)
                    .join(Run)
                    .where(Run.logical_date >= "2026-10-26")
                )
            )
            for extraction in extractions[:1] if problem == "mixed" else extractions:
                extraction.version = "rules:changed-version"
        else:
            snapshots = list(
                db.scalars(
                    select(Snapshot)
                    .join(Run)
                    .where(Run.logical_date >= "2026-10-26")
                    .order_by(Snapshot.id)
                )
            )
            for snapshot in snapshots[:6]:
                if problem == "coverage":
                    db.scalar(
                        select(Extraction).where(Extraction.snapshot_id == snapshot.id)
                    ).status = "quarantined"
                else:
                    raw = {**snapshot.raw, "descriptionStale": True}
                    snapshot.raw = raw
    comparison = weekly(two_weeks)["overview"]["comparison"]
    assert comparison["prevalence_status"] == "insufficient_data"
    assert all(c["percentage_point_change"] is None for c in comparison["changes"])
    assert comparison["status"] == ("insufficient_data" if problem == "capped" else "ready")


def test_small_sample_empty_week_and_live_isolation(context, rows, clock):
    factory, settings, mid, _ = context
    with factory.begin() as db:
        run = enqueue(db, mid, settings, clock)
        ingest(db, run, rows, settings, clock)
        run.state = "completed"
    comparison = weekly(context)["overview"]["comparison"]
    assert comparison["status"] == "insufficient_data"
    assert comparison["volume"]["relative_change"] is None
    live = weekly(context, mode="live")
    assert all(w["eligible"] == 0 for w in live["history"])
    assert live["overview"]["comparison"]["changes"] == []


def test_successful_empty_days_are_complete_zeros(context):
    factory, settings, mid, _ = context
    with factory.begin() as db:
        for day in range(14):
            clock = datetime(2026, 10, 19, 12, tzinfo=UTC) + timedelta(days=day)
            run = enqueue(db, mid, settings, clock)
            run.state = "completed"
            run.finished_at = clock.isoformat()
    result = weekly(context)
    comparison = result["overview"]["comparison"]
    assert comparison["current"]["complete"] and comparison["previous"]["complete"]
    assert comparison["current"]["eligible"] == comparison["previous"]["eligible"] == 0
    assert comparison["current"]["missing_days"] == []
    assert comparison["volume"] == {"change": None, "relative_change": None}
    assert result["history"][-1]["coverage"] is None


def test_query_edit_never_combines_versions(two_weeks):
    factory, _, mid, qid = two_weeks
    with factory.begin() as db:
        original = db.get(QueryVersion, qid)
        new = QueryVersion(monitor_id=mid, version=2, config=original.config)
        db.add(new)
        db.flush()
        for run in db.scalars(select(Run).where(Run.logical_date >= "2026-10-26")):
            run.query_version_id = new.id
            for observation in db.scalars(select(Observation).where(Observation.run_id == run.id)):
                observation.query_version_id = new.id
    comparison = weekly(two_weeks)["overview"]["comparison"]
    assert comparison["current"]["eligible"] == 0
    assert comparison["status"] == "insufficient_data"


def test_history_uses_each_weeks_snapshot_not_the_latest_job_text(two_weeks):
    factory, settings, mid, _ = two_weeks
    with factory.begin() as db:
        run = db.scalar(select(Run).where(Run.logical_date == "2026-10-26"))
        clock = datetime(2026, 10, 26, 13, tzinfo=UTC)
        ingest(
            db,
            run,
            [
                {
                    "id": "0-0",
                    "title": "Engineer",
                    "companyName": "Example",
                    "descriptionText": "Requirements\nExperience with Python and LLMs.",
                }
            ],
            settings,
            clock,
        )
    comparison = weekly(two_weeks)["overview"]["comparison"]
    assert all(t["theme"] != "ai_engineering" for t in comparison["previous"]["themes"])
    ai = next(t for t in comparison["current"]["themes"] if t["theme"] == "ai_engineering")
    assert ai["count"] == 22
    assert comparison["previous"]["eligible"] == 35
    assert comparison["current"]["eligible"] == 43


def test_partial_enrichment_can_complete_a_day_and_repeated_jobs_deduplicate(two_weeks):
    factory, settings, mid, _ = two_weeks
    with factory.begin() as db:
        run = db.scalar(select(Run).where(Run.logical_date == "2026-10-28"))
        run.state = "partial"
        run.diagnostics = {"listing_status": "completed"}
        clock = datetime(2026, 10, 29, 13, tzinfo=UTC)
        repeat = enqueue(db, mid, settings, clock, kind="weekly")
        ingest(
            db,
            repeat,
            [
                {
                    "id": "9-0",
                    "title": "Engineer",
                    "companyName": "Example",
                    "descriptionText": "Requirements\nExperience with Python. Experience with LLMs and API design.",
                }
            ],
            settings,
            clock,
        )
        repeat.state = "completed"
    comparison = weekly(two_weeks)["overview"]["comparison"]
    assert comparison["current"]["eligible"] == 42
    assert comparison["status"] == "ready"


def test_sunday_is_excluded_until_it_finishes_and_historical_end_is_respected(
    two_weeks, monkeypatch
):
    class Sunday(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 11, 1, 23, tzinfo=UTC).astimezone(tz)

    monkeypatch.setattr(analytics, "datetime", Sunday)
    result = weekly(two_weeks)
    assert result["history"][-1]["to"] == "2026-10-25"
    factory, _, _, qid = two_weeks
    with factory() as db:
        historical = compute(db, qid, date(2026, 10, 19), date(2026, 10, 25))
        assert len(historical["history"]) == 1
        assert historical["overview"]["comparison"]["current"]["to"] == "2026-10-25"


def test_required_filter_preserves_alternatives_and_theme_deduplication(two_weeks):
    factory, _, _, _ = two_weeks
    with factory.begin() as db:
        extraction = db.scalar(
            select(Extraction).join(Snapshot).join(Run).where(Run.logical_date == "2026-10-26")
        )
        requirement = extraction.result["requirements"][0]
        duplicate = {**requirement, "alternatives_group": "choice", "requiredness": "required"}
        extraction.result = {**extraction.result, "requirements": [requirement, duplicate]}
    result = weekly(two_weeks, requiredness="required")
    assert all(not s["alternative"] for w in result["history"] for s in w["skills"])
    assert all(t["count"] <= w["parsed"] for w in result["history"] for t in w["themes"])
