from collections import Counter, defaultdict
from datetime import UTC, datetime, time, timedelta
from statistics import median
from zoneinfo import ZoneInfo

from sqlalchemy import select

from market.models import (
    Extraction,
    Metric,
    Observation,
    QueryVersion,
    Run,
    Snapshot,
    SourceJob,
    now,
)

METHOD = "unique-observed-jobs-v1"


def cohort(db, qv_id, start=None, end=None, mode="offline", states=("completed",)):
    qv = db.get(QueryVersion, qv_id)
    tz = ZoneInfo(qv.config["timezone"])
    end = end or datetime.now(tz).date()
    start = start or end - timedelta(days=27)
    left = datetime.combine(start, time.min, tzinfo=tz).astimezone(UTC).isoformat()
    right = (
        datetime.combine(end + timedelta(days=1), time.min, tzinfo=tz).astimezone(UTC).isoformat()
    )
    records = db.execute(
        select(Observation, SourceJob, Snapshot)
        .join(SourceJob, SourceJob.id == Observation.job_id)
        .join(Snapshot, Snapshot.id == Observation.snapshot_id)
        .join(Run, Run.id == Observation.run_id)
        .where(
            Observation.query_version_id == qv_id,
            Observation.observed_at >= left,
            Observation.observed_at < right,
            Run.mode == mode,
            Run.state.in_(states),
        )
        .order_by(Observation.observed_at, Observation.id)
    ).all()
    latest = {}
    first: dict[str, str] = {}
    for observation, job, snapshot in records:
        first.setdefault(job.id, observation.observed_at)
        latest[job.id] = (observation, job, snapshot)
    # First observation in this query version, across all successful runs.
    all_obs = db.execute(
        select(Observation.job_id, Observation.observed_at)
        .join(Run, Run.id == Observation.run_id)
        .where(Observation.query_version_id == qv_id, Run.mode == mode, Run.state == "completed")
        .order_by(Observation.observed_at)
    ).all()
    for job_id, stamp in reversed(all_obs):
        first[job_id] = stamp
    return list(latest.values()), first, start, end, tz


def compute(db, qv_id, start=None, end=None, mode="offline", requiredness=None):
    rows, first, start, end, tz = cohort(db, qv_id, start, end, mode)
    collected, _, _, _, _ = cohort(db, qv_id, start, end, mode, ("completed", "partial"))
    collected_parsed = sum(
        db.scalar(
            select(Extraction.id)
            .where(Extraction.snapshot_id == snapshot.id, Extraction.status == "valid")
            .limit(1)
        )
        is not None
        for _, _, snapshot in collected
    )
    runs = list(
        db.scalars(
            select(Run)
            .where(
                Run.query_version_id == qv_id,
                Run.mode == mode,
                Run.logical_date >= start.isoformat(),
                Run.logical_date <= end.isoformat(),
            )
            .order_by(Run.created_at.desc())
        )
    )
    eligible = len(rows)
    parsed = 0
    skills = defaultdict(set)
    examples: dict[tuple, list[dict]] = defaultdict(list)
    titles: Counter[str] = Counter()
    seniorities: Counter[str] = Counter()
    arrangements: Counter[str] = Counter()
    pay_values = defaultdict(list)
    extraction_versions: set[str] = set()
    disclosures = set()
    daily: Counter[str] = Counter()
    for observation, job, snapshot in rows:
        first_date = datetime.fromisoformat(first[job.id]).astimezone(tz).date()
        if start <= first_date <= end:
            daily[first_date.isoformat()] += 1
        titles[snapshot.raw.get("title") or "Unknown"] += 1
        extraction = db.scalar(
            select(Extraction)
            .where(Extraction.snapshot_id == snapshot.id, Extraction.status == "valid")
            .order_by(Extraction.created_at.desc(), Extraction.id.desc())
        )
        if not extraction:
            seniorities["unknown"] += 1
            arrangements["unspecified"] += 1
            continue
        parsed += 1
        data = extraction.result
        extraction_versions.add(extraction.version)
        seniorities[data["seniority"]] += 1
        arrangements[data["work_arrangement"]] += 1
        for req in data["requirements"]:
            if (
                req["explicit_negation"]
                or req["context"] != "candidate_requirement"
                or req["confidence"] < 0.7
            ):
                continue
            if requiredness and req["requiredness"] != requiredness:
                continue
            # Alternative members never count as independent strict requirements.
            if requiredness == "required" and req["alternatives_group"]:
                continue
            key = (req["canonical_candidate"], req["requiredness"], bool(req["alternatives_group"]))
            skills[key].add(job.source_job_id)
            if len(examples[key]) < 3:
                examples[key].append(
                    {
                        "job_id": job.source_job_id,
                        "quote": req["evidence_quote"],
                        "alternatives_group": req["alternatives_group"],
                    }
                )
        for pay in data["compensation"]:
            if pay["explicit_in_job_ad"]:
                disclosures.add(job.id)
            if (
                pay["explicit_in_job_ad"]
                and pay["type"] == "base_salary"
                and pay["currency_iso"]
                and pay["period"] != "unknown"
                and pay["employment_basis"] == "full_time"
                and pay["min_amount"] is not None
                and pay["max_amount"] is not None
            ):
                matching = [
                    p
                    for p in data["compensation"]
                    if p["currency_iso"] == pay["currency_iso"] and p["period"] == pay["period"]
                ]
                if len(matching) == 1:
                    pay_values[(pay["currency_iso"], pay["period"])].append(
                        (pay["min_amount"] + pay["max_amount"]) / 2
                    )
    days = []
    day = start
    healthy_dates = {r.logical_date for r in runs if r.state == "completed" and r.kind == "daily"}
    while day <= end:
        key = day.isoformat()
        days.append(
            {
                "date": key,
                "newly_observed": daily.get(key, 0) if key in healthy_dates else None,
                "partial_day": day == datetime.now(tz).date(),
                "capped": any(r.logical_date == key and r.diagnostics.get("capped") for r in runs),
            }
        )
        day += timedelta(days=1)
    completed = [r for r in runs if r.state == "completed"]
    latest = completed[0].finished_at if completed else None
    return {
        "meta": {
            "collected_jobs": len(collected),
            "latest_collection_at": next((r.finished_at for r in runs if r.finished_at), None),
            "collected_parsed": collected_parsed,
            "partial_only_jobs": len(
                {job.id for _, job, _ in collected} - {job.id for _, job, _ in rows}
            ),
            "query_version_id": qv_id,
            "from": start.isoformat(),
            "to": end.isoformat(),
            "timezone": str(tz),
            "eligible": eligible,
            "parsed": parsed,
            "extraction_coverage": parsed / eligible if eligible else None,
            "methodology": METHOD,
            "extraction_versions": sorted(extraction_versions),
            "mixed_extraction_versions": len(extraction_versions) > 1,
            "computed_at": now(),
            "data_as_of": latest,
            "demo": mode == "offline",
            "small_sample": eligible < 30,
            "comparison": "insufficient_data",
            "incomplete_runs": sum(r.state != "completed" for r in runs),
            "capped": any(r.diagnostics.get("capped") for r in runs),
            "stale": not latest
            or datetime.now(UTC) - datetime.fromisoformat(latest) > timedelta(hours=36),
        },
        "overview": {
            "jobs": eligible,
            "parsed": parsed,
            "salary_disclosed": len(disclosures),
            "salary_disclosure_rate": len(disclosures) / eligible if eligible else None,
            "remote": arrangements["remote"],
            "remote_classifiable": sum(v for k, v in arrangements.items() if k != "unspecified"),
            "daily": days,
        },
        "skills": [
            {
                "skill": k[0],
                "requiredness": k[1],
                "alternative": k[2],
                "count": len(ids),
                "denominator": parsed,
                "percentage": len(ids) / parsed if parsed else None,
                "examples": examples[k],
            }
            for k, ids in sorted(skills.items(), key=lambda item: (-len(item[1]), item[0]))
        ],
        "titles": {
            "titles": dict(titles),
            "seniority": dict(seniorities),
            "work_arrangements": dict(arrangements),
        },
        "salaries": {
            "disclosed": len(disclosures),
            "eligible": eligible,
            "groups": [
                {
                    "currency": currency,
                    "period": period,
                    "n": len(values),
                    "median": median(values) if len(values) >= 3 else None,
                    "min": min(values),
                    "max": max(values),
                }
                for (currency, period), values in pay_values.items()
            ],
            "method": "Explicit full-time base salary midpoints; no FX or period conversion; median requires N >= 3",
        },
        "insights": [
            {
                "status": "insufficient_data" if eligible < 30 else "tentative",
                "text": f"{parsed} of {eligible} observed jobs have a validated extraction. Capped searches describe this collected sample only.",
                "job_ids": [job.source_job_id for _, job, _ in rows[:3]],
            }
        ],
    }


def materialize(db, qv_id):
    for mode in ("offline", "live"):
        result = compute(db, qv_id, mode=mode)
        period = f"{result['meta']['from']}:{result['meta']['to']}:{mode}"
        metric = db.scalar(
            select(Metric).where(
                Metric.query_version_id == qv_id, Metric.period == period, Metric.method == METHOD
            )
        )
        if not metric:
            metric = Metric(query_version_id=qv_id, period=period, method=METHOD, result=result)
            db.add(metric)
        metric.result, metric.computed_at = result, now()
