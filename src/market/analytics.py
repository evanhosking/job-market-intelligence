from collections import Counter, defaultdict
from datetime import UTC, datetime, time, timedelta
from statistics import median
from zoneinfo import ZoneInfo

from sqlalchemy import and_, case, or_, select

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
from market.taxonomy import TAXONOMY_VERSION, THEMES, themed_result

METHOD = "complete-weeks-v4"


def listing_eligible():
    return or_(
        Run.state == "completed",
        and_(Run.state == "partial", Run.diagnostics["listing_status"].as_string() == "completed"),
    )


def cohort(db, qv_id, start=None, end=None, mode="offline", states=None):
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
            Run.state.in_(states) if states else listing_eligible(),
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
        .where(Observation.query_version_id == qv_id, Run.mode == mode, listing_eligible())
        .order_by(Observation.observed_at)
    ).all()
    for job_id, stamp in reversed(all_obs):
        first[job_id] = stamp
    return list(latest.values()), first, start, end, tz


def observation_payload(db, observation, snapshot):
    run = db.get(Run, observation.run_id)
    return next(
        (raw for raw in run.raw_checkpoint if raw.get("id") == snapshot.raw.get("id")), snapshot.raw
    )


def _compute_window(db, qv_id, start=None, end=None, mode="offline", requiredness=None):
    rows, first, start, end, tz = cohort(db, qv_id, start, end, mode)
    collected, _, _, _, _ = cohort(db, qv_id, start, end, mode, ("completed", "partial"))
    parsed_snapshots = set(
        db.scalars(
            select(Extraction.snapshot_id).where(
                Extraction.snapshot_id.in_([snapshot.id for _, _, snapshot in collected]),
                Extraction.status == "valid",
            )
        )
    )
    collected_parsed = sum(snapshot.id in parsed_snapshots for _, _, snapshot in collected)
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
    theme_jobs = defaultdict(set)
    theme_context_jobs = defaultdict(set)
    theme_skills = defaultdict(set)
    theme_examples: dict[str, list[dict]] = defaultdict(list)
    titles: Counter[str] = Counter()
    seniorities: Counter[str] = Counter()
    arrangements: Counter[str] = Counter()
    pay_values = defaultdict(list)
    extraction_versions: set[str] = set()
    disclosures = set()
    daily: Counter[str] = Counter()
    snapshot_ids = [snapshot.id for _, _, snapshot in rows]
    extractions: dict[str, Extraction] = {}
    for candidate in db.scalars(
        select(Extraction)
        .where(Extraction.snapshot_id.in_(snapshot_ids), Extraction.status == "valid")
        .order_by(
            case((Extraction.version.like("llm:%"), 0), else_=1),
            Extraction.created_at.desc(),
            Extraction.id.desc(),
        )
    ):
        extractions.setdefault(candidate.snapshot_id, candidate)
    for observation, job, snapshot in rows:
        first_date = datetime.fromisoformat(first[job.id]).astimezone(tz).date()
        if start <= first_date <= end:
            daily[first_date.isoformat()] += 1
        titles[snapshot.raw.get("title") or "Unknown"] += 1
        extraction = extractions.get(snapshot.id)
        if not extraction or observation_payload(db, observation, snapshot).get("descriptionStale"):
            seniorities["unknown"] += 1
            arrangements["unspecified"] += 1
            continue
        parsed += 1
        data = themed_result(extraction.result)
        extraction_versions.add(extraction.version)
        seniorities[data["seniority"]] += 1
        arrangements[data["work_arrangement"]] += 1
        for req in data["requirements"]:
            if (
                req["explicit_negation"]
                or req["context"] in {"benefit", "company_description"}
                or req["confidence"] < (0.7 if req["context"] == "candidate_requirement" else 0.5)
            ):
                continue
            if requiredness and (
                req["requiredness"] != requiredness or req["context"] != "candidate_requirement"
            ):
                continue
            # Alternative members never count as independent strict requirements.
            if requiredness == "required" and req["alternatives_group"]:
                continue
            key = (
                req["canonical_candidate"],
                req["requiredness"],
                bool(req["alternatives_group"]),
                req["theme"],
                req["subtheme"],
                req["context"],
            )
            theme_jobs[req["theme"]].add(job.source_job_id)
            theme_context_jobs[(req["theme"], req["context"])].add(job.source_job_id)
            theme_skills[req["theme"]].add(req["canonical_candidate"])
            skills[key].add(job.source_job_id)
            example = {
                "job_id": job.source_job_id,
                "snapshot_id": snapshot.id,
                "quote": req["evidence_quote"],
                "alternatives_group": req["alternatives_group"],
            }
            if len(examples[key]) < 3:
                examples[key].append(example)
            if len(theme_examples[req["theme"]]) < 3 and not any(
                e["job_id"] == job.source_job_id for e in theme_examples[req["theme"]]
            ):
                theme_examples[req["theme"]].append(example)
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
    healthy_dates = {
        r.logical_date
        for r in runs
        if (
            r.state == "completed"
            or (r.state == "partial" and r.diagnostics.get("listing_status") == "completed")
        )
        and (r.kind == "daily" or r.kind.startswith("manual-"))
    }
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
    completed = [
        r
        for r in runs
        if r.state == "completed"
        or (r.state == "partial" and r.diagnostics.get("listing_status") == "completed")
    ]
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
            "description_coverage": parsed / eligible if eligible else None,
            "parsed": parsed,
            "extraction_coverage": parsed / eligible if eligible else None,
            "methodology": METHOD,
            "theme_taxonomy_version": TAXONOMY_VERSION,
            "extraction_versions": sorted(extraction_versions),
            "mixed_extraction_versions": len(extraction_versions) > 1,
            "computed_at": now(),
            "data_as_of": latest,
            "demo": mode == "offline",
            "small_sample": eligible < 30,
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
            "job_ids": [job.source_job_id for _, job, _ in rows[:3]],
        },
        "skills": [
            {
                "skill": k[0],
                "requiredness": k[1],
                "alternative": k[2],
                "theme": k[3],
                "theme_label": THEMES[k[3]],
                "subtheme": k[4],
                "context": k[5],
                "count": len(ids),
                "denominator": parsed,
                "percentage": len(ids) / parsed if parsed else None,
                "examples": examples[k],
            }
            for k, ids in sorted(skills.items(), key=lambda item: (-len(item[1]), item[0]))
        ],
        "themes": [
            {
                "theme": theme,
                "label": THEMES[theme],
                "count": len(ids),
                "denominator": parsed,
                "percentage": len(ids) / parsed if parsed else None,
                "requirement_count": len(theme_context_jobs[(theme, "candidate_requirement")]),
                "responsibility_count": len(theme_context_jobs[(theme, "responsibility")]),
                "stack_count": len(theme_context_jobs[(theme, "current_stack")]),
                "mentioned_count": len(theme_context_jobs[(theme, "other")]),
                "skills": sorted(theme_skills[theme]),
                "examples": theme_examples[theme],
            }
            for theme, ids in sorted(theme_jobs.items(), key=lambda item: (-len(item[1]), item[0]))
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


def _week(db, qv_id, end, mode, requiredness):
    start = end - timedelta(days=6)
    result = _compute_window(db, qv_id, start, end, mode, requiredness)
    meta = result["meta"]
    missing = [d["date"] for d in result["overview"]["daily"] if d["newly_observed"] is None]
    return {
        "from": start.isoformat(),
        "to": end.isoformat(),
        "eligible": meta["eligible"],
        "parsed": meta["parsed"],
        "coverage": meta["extraction_coverage"],
        "complete": not missing,
        "missing_days": missing,
        "capped": meta["capped"],
        "extraction_versions": meta["extraction_versions"],
        "skills": result["skills"],
        "themes": result["themes"],
        "job_ids": result["overview"]["job_ids"],
    }


def _weekly_trends(db, qv_id, start, end, tz, mode, requiredness):
    # Calendar arithmetic keeps Monday–Sunday windows intact across DST changes.
    yesterday = datetime.now(tz).date() - timedelta(days=1)
    last_day = min(end, yesterday)
    sunday = last_day - timedelta(days=(last_day.weekday() + 1) % 7)
    count = max(1, (end - start).days // 7 + 1)
    weeks = [
        _week(db, qv_id, sunday - timedelta(weeks=i), mode, requiredness)
        for i in range(max(2, count) - 1, -1, -1)
    ]
    previous, current = weeks[-2:]
    reasons = []
    if not all(w["complete"] for w in (previous, current)):
        reasons.append(
            "Both weeks need a completed daily listing collection on all seven days for this query version."
        )
    if any(w["capped"] for w in (previous, current)):
        reasons.append("A result cap was reached; the collected weeks may not be comparable.")
    if any(w["eligible"] < 30 for w in (previous, current)):
        reasons.append("At least 30 distinct observed jobs are needed in each week.")
    prevalence_reasons = reasons.copy()
    if any(w["parsed"] < 30 for w in (previous, current)):
        prevalence_reasons.append(
            "Skill and theme comparisons need at least 30 parsed descriptions in each week."
        )
    versions = set(previous["extraction_versions"] + current["extraction_versions"])
    if len(versions) != 1:
        prevalence_reasons.append("Both weeks must use the same single extraction version.")
    if (
        previous["coverage"] is not None
        and current["coverage"] is not None
        and abs(current["coverage"] - previous["coverage"]) > 0.10 + 1e-9
    ):
        prevalence_reasons.append("Extraction coverage differs by more than 10 percentage points.")
    ready = not reasons
    prevalence_ready = not prevalence_reasons
    before, after = previous["eligible"], current["eligible"]
    changes = []
    for kind, fields in (
        ("themes", ("theme",)),
        ("skills", ("skill", "context", "requiredness", "alternative")),
    ):
        prior = {tuple(row[f] for f in fields): row for row in previous[kind]}
        latest = {tuple(row[f] for f in fields): row for row in current[kind]}
        for key in sorted(prior.keys() | latest.keys()):
            old, new = prior.get(key), latest.get(key)
            old_share = (
                (old["count"] if old else 0) / previous["parsed"] if previous["parsed"] else None
            )
            new_share = (
                (new["count"] if new else 0) / current["parsed"] if current["parsed"] else None
            )
            changes.append(
                {
                    "kind": kind,
                    "label": (latest[key] if key in latest else prior[key])[
                        "label" if kind == "themes" else "skill"
                    ],
                    **{f: key[i] for i, f in enumerate(fields)},
                    "previous_count": old["count"] if old else 0,
                    "current_count": new["count"] if new else 0,
                    "previous_share": old_share,
                    "current_share": new_share,
                    "percentage_point_change": (new_share - old_share) * 100
                    if prevalence_ready and new_share is not None and old_share is not None
                    else None,
                    "previous_examples": old["examples"] if old else [],
                    "current_examples": new["examples"] if new else [],
                }
            )
    comparison = {
        "status": "ready" if ready else "insufficient_data",
        "reasons": reasons,
        "prevalence_status": "ready" if prevalence_ready else "insufficient_data",
        "prevalence_reasons": prevalence_reasons,
        "previous": previous,
        "current": current,
        "volume": {
            "change": after - before if ready else None,
            "relative_change": (after - before) / before if ready and before else None,
        },
        "changes": changes,
    }
    findings = []
    period = f"{current['from']}–{current['to']} versus {previous['from']}–{previous['to']}"
    if ready:
        findings.append(
            {
                "status": "tentative",
                "text": f"{after} distinct jobs observed versus {before} ({after - before:+d}) in {period}. These are collected sightings, not newly published vacancies or total market demand.",
                "job_ids": list(dict.fromkeys(current["job_ids"] + previous["job_ids"])),
                "examples": [],
            }
        )
    if prevalence_ready:
        for kind, limit in (("themes", 2), ("skills", 1)):
            ranked = sorted(
                (
                    c
                    for c in changes
                    if c["kind"] == kind and abs(c["percentage_point_change"]) >= 0.1
                ),
                key=lambda c: (-abs(c["percentage_point_change"]), c["label"]),
            )[:limit]
            for change in ranked:
                examples = [
                    {**e, "period": label}
                    for label in ("previous", "current")
                    for e in change[f"{label}_examples"][:1]
                ]
                interpretation = "evidenced mentions"
                if kind == "skills":
                    interpretation = (
                        f"{change['context'].replace('_', ' ')} · {change['requiredness']}"
                    )
                    if change["alternative"]:
                        interpretation += " · alternative"
                findings.append(
                    {
                        "status": "tentative",
                        "text": f"{change['label']} ({interpretation}): {change['current_count']}/{current['parsed']} parsed jobs ({change['current_share']:.0%}) versus {change['previous_count']}/{previous['parsed']} ({change['previous_share']:.0%}), {change['percentage_point_change']:+.1f} percentage points in {period}. Extraction coverage: {current['coverage']:.0%} versus {previous['coverage']:.0%}. This describes the parsed sample; no statistical significance is claimed.",
                        "job_ids": list(dict.fromkeys(e["job_id"] for e in examples)),
                        "examples": examples,
                    }
                )
    if prevalence_reasons:
        findings.append(
            {
                "status": "insufficient_data",
                "text": f"Weekly skill/theme comparison unavailable for {period}. "
                + " ".join(prevalence_reasons),
                "job_ids": [],
                "examples": [],
            }
        )
    return weeks[-count:], comparison, findings


def compute(
    db, qv_id, start=None, end=None, mode="offline", requiredness=None, *, include_history=True
):
    result = _compute_window(db, qv_id, start, end, mode, requiredness)
    if include_history:
        meta = result["meta"]
        history, comparison, findings = _weekly_trends(
            db,
            qv_id,
            datetime.fromisoformat(meta["from"]).date(),
            datetime.fromisoformat(meta["to"]).date(),
            ZoneInfo(meta["timezone"]),
            mode,
            requiredness,
        )
        result["history"] = history
        result["overview"]["comparison"] = comparison
        result["overview"]["findings"] = findings
        result["meta"]["comparison"] = comparison["status"]
        result["insights"] = findings + result["insights"]
    return result


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
