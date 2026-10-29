import hashlib
import json
import logging
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from linkedin_jobs_cli.client import LinkedInPublicClient
from sqlalchemy import select, update

from market.contracts import MonitorConfig
from market.extraction import ALIASES, clean_description, extract, version
from market.models import (
    Budget,
    Compensation,
    Extraction,
    Monitor,
    Observation,
    Quarantine,
    QueryVersion,
    Run,
    Skill,
    SkillAlias,
    SkillMention,
    Snapshot,
    SourceJob,
    now,
    uid,
)

FIXTURE = Path(__file__).resolve().parents[2] / "tests/fixtures/jobs.json"


def enqueue(db, monitor_id, settings, clock=None, kind="daily"):
    clock = clock or datetime.now(UTC)
    monitor = db.scalar(select(Monitor).where(Monitor.id == monitor_id).with_for_update())
    if not monitor or not monitor.enabled:
        raise ValueError("Monitor not found or disabled")
    qv = db.scalar(
        select(QueryVersion).where(
            QueryVersion.monitor_id == monitor_id, QueryVersion.version == monitor.current_version
        )
    )
    config = MonitorConfig.model_validate(qv.config)
    local = clock.astimezone(ZoneInfo(config.timezone))
    logical_date = local.date().isoformat()
    existing = db.scalar(
        select(Run).where(
            Run.monitor_id == monitor_id,
            Run.logical_date == logical_date,
            Run.kind == kind,
            Run.mode == settings.collection_mode,
        )
    )
    if existing:
        return existing
    active = db.scalar(
        select(Run).where(Run.monitor_id == monitor_id, Run.state.in_(["queued", "running"]))
    )
    if active:
        return active
    # Atomic global reservation: failed runs retain reservation because usage may be uncertain.
    month = clock.strftime("%Y-%m")
    if not db.get(Budget, month):
        # On Postgres the advisory transaction lock serializes first creation across monitors.
        if db.bind.dialect.name == "postgresql":
            from sqlalchemy import text

            db.execute(text("SELECT pg_advisory_xact_lock(731901)"))
        if not db.get(Budget, month):
            db.add(Budget(month=month, reserved=0, llm_reserved=0))
            db.flush()
    llm_reservation = (
        config.max_items
        if settings.llm_mode == "live" and settings.collection_mode == "live"
        else 0
    )
    reserved = db.execute(
        update(Budget)
        .where(
            Budget.month == month,
            Budget.reserved + config.max_items <= settings.monthly_item_cap,
            Budget.llm_reserved + llm_reservation <= settings.llm_monthly_request_cap,
        )
        .values(
            reserved=Budget.reserved + config.max_items,
            llm_reserved=Budget.llm_reserved + llm_reservation,
        )
    )
    if reserved.rowcount != 1:
        raise ValueError("Monthly item or LLM request budget exhausted")
    source = config.source(kind)
    run = Run(
        monitor_id=monitor_id,
        query_version_id=qv.id,
        logical_date=logical_date,
        kind=kind,
        mode=settings.collection_mode,
        input={
            "config": qv.config,
            "params": source.params,
            "warnings": source.warnings,
            "provider": "linkedin-jobs-cli/0.1.0+diagnostics-v1",
        },
        reserved_items=config.max_items,
        created_at=clock.isoformat(),
    )
    db.add(run)
    db.flush()
    return run


def digest(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()


def ingest(db, run, rows, settings, clock=None):
    # Offline replay must never invoke a paid model, even with live keys configured.
    if run.mode == "offline":
        settings = settings.model_copy(update={"llm_mode": "rules"})
    stamp = (clock or datetime.now(UTC)).isoformat()
    counts = {"fetched": len(rows), "quarantined": 0, "extracted": 0}
    if not isinstance(rows, list):
        raise ValueError("Dataset must be an array")
    if len(rows) > run.reserved_items:
        raise ValueError("Dataset exceeds reserved item cap")
    for raw in rows:
        if (
            not isinstance(raw, dict)
            or not isinstance(raw.get("id"), str)
            or not raw["id"].strip()
            or len(raw["id"]) > 100
            or not isinstance(raw.get("title"), str)
            or not raw["title"].strip()
        ):
            payload = raw if isinstance(raw, dict) else {"invalid": raw}
            h = digest(payload)
            if not db.scalar(
                select(Quarantine).where(Quarantine.run_id == run.id, Quarantine.payload_hash == h)
            ):
                db.add(
                    Quarantine(
                        run_id=run.id,
                        payload_hash=h,
                        raw=payload,
                        reason="Missing or invalid string identity/title",
                    )
                )
            counts["quarantined"] += 1
            continue
        if db.scalar(
            select(Observation).where(
                Observation.run_id == run.id,
                Observation.job_id.in_(
                    select(SourceJob.id).where(
                        SourceJob.source == "linkedin", SourceJob.source_job_id == raw["id"]
                    )
                ),
            )
        ):
            continue
        job = db.scalar(
            select(SourceJob).where(
                SourceJob.source == "linkedin", SourceJob.source_job_id == raw["id"]
            )
        )
        if not job:
            job = SourceJob(
                source_job_id=raw["id"],
                title=raw["title"],
                company=raw.get("companyName") or "Unknown",
                location=raw.get("location") or "Unknown",
                url="https://www.linkedin.com/jobs/view/" + raw["id"]
                if raw["id"].isdigit()
                else "https://www.linkedin.com/jobs/search/",
                first_seen_at=stamp,
                last_seen_at=stamp,
            )
            db.add(job)
            db.flush()
        job.last_seen_at = stamp
        job.title = raw["title"]
        job.company = raw.get("companyName") or "Unknown"
        job.location = raw.get("location") or "Unknown"
        posted = raw.get("postedAt")
        try:
            if posted:
                parsed = datetime.fromisoformat(posted)
                if parsed.date() <= datetime.fromisoformat(stamp).date():
                    job.posted_at = posted  # date-only strings stay date-only
        except (ValueError, TypeError):
            pass
        text = clean_description(raw)
        # Exclude volatile tracking, scrape time and applicant counts from semantic hash.
        material = {
            key: raw.get(key)
            for key in [
                "title",
                "companyName",
                "location",
                "salary",
                "salaryInfo",
                "seniorityLevel",
                "employmentType",
            ]
        }
        material["description"] = text
        content_hash = digest(material)
        snapshot = db.scalar(
            select(Snapshot).where(Snapshot.job_id == job.id, Snapshot.content_hash == content_hash)
        )
        if not snapshot:
            snapshot = Snapshot(
                job_id=job.id,
                run_id=run.id,
                raw=raw,
                description=text,
                content_hash=content_hash,
                observed_at=stamp,
            )
            db.add(snapshot)
            db.flush()
        extraction = db.scalar(
            select(Extraction).where(
                Extraction.snapshot_id == snapshot.id, Extraction.version == version(settings)
            )
        )
        if not extraction:
            try:
                result = extract(raw, settings).model_dump(mode="json")
                extraction = Extraction(
                    snapshot_id=snapshot.id,
                    version=version(settings),
                    status="valid",
                    result=result,
                )
            except Exception as exc:
                # Do not persist HTTP errors that may include endpoint tokens or provider bodies.
                extraction = Extraction(
                    snapshot_id=snapshot.id,
                    version=version(settings),
                    status="quarantined",
                    result={},
                    error=type(exc).__name__,
                )
            db.add(extraction)
            db.flush()
            if extraction.status == "valid":
                counts["extracted"] += 1
                for ordinal, req in enumerate(extraction.result["requirements"]):
                    name = req["canonical_candidate"]
                    if not db.get(Skill, name):
                        db.add(Skill(id=name))
                        db.flush()
                        for alias in ALIASES.get(name, [name]):
                            if not db.get(SkillAlias, alias.casefold()):
                                db.add(SkillAlias(alias=alias.casefold(), skill_id=name))
                    db.add(
                        SkillMention(
                            extraction_id=extraction.id,
                            ordinal=ordinal,
                            skill_id=name,
                            evidence=req,
                        )
                    )
                for pay in extraction.result["compensation"]:
                    db.add(Compensation(extraction_id=extraction.id, disclosure=pay))
        db.add(
            Observation(
                run_id=run.id,
                query_version_id=run.query_version_id,
                job_id=job.id,
                snapshot_id=snapshot.id,
                observed_at=stamp,
            )
        )
        db.flush()
    return counts


def schedule(db, settings, clock=None):
    clock = clock or datetime.now(UTC)
    for monitor in db.scalars(select(Monitor).where(Monitor.enabled.is_(True))):
        qv = db.scalar(
            select(QueryVersion).where(
                QueryVersion.monitor_id == monitor.id,
                QueryVersion.version == monitor.current_version,
            )
        )
        c = MonitorConfig.model_validate(qv.config)
        local = clock.astimezone(ZoneInfo(c.timezone))
        due = datetime.combine(
            local.date(),
            datetime.strptime(c.daily_time, "%H:%M").time(),
            tzinfo=ZoneInfo(c.timezone),
        )
        # Catch up only today's due schedule. Older dates remain visibly missing, never fabricated.
        if local >= due:
            try:
                with db.begin_nested():
                    enqueue(db, monitor.id, settings, clock)
                    if c.weekly_overlap and local.weekday() == 6:
                        enqueue(db, monitor.id, settings, clock, "weekly")
            except ValueError:
                logging.warning(
                    "schedule_skipped monitor_id=%s reason=budget_or_disabled", monitor.id
                )


def work_once(factory, settings, clock=None):
    clock = clock or datetime.now(UTC)
    owner = uid()
    with factory.begin() as db:
        # Expired uncertain requests are never automatically repeated.
        for expired in db.scalars(
            select(Run)
            .where(Run.state == "running", Run.lease_until < clock.isoformat())
            .with_for_update(skip_locked=True)
        ):
            expired.state, expired.error = (
                "interrupted",
                "Worker lease expired; replay checkpoint or explicitly run on another day",
            )
        run = db.scalar(
            select(Run)
            .where(Run.state == "queued")
            .order_by(Run.created_at, Run.id)
            .with_for_update(skip_locked=True)
        )
        if not run:
            return False
        run.state, run.lease_owner = "running", owner
        run.lease_until = (clock + timedelta(minutes=2)).isoformat()
        run_id, config, kind, mode = (
            run.id,
            MonitorConfig.model_validate(run.input["config"]),
            run.kind,
            run.mode,
        )
    started = datetime.now(UTC)

    def heartbeat():
        if datetime.now(UTC) - started > timedelta(minutes=20):
            raise RuntimeError("Collection wall-time budget exhausted")
        with factory.begin() as db:
            changed = db.execute(
                update(Run)
                .where(Run.id == run_id, Run.lease_owner == owner, Run.state == "running")
                .values(lease_until=(datetime.now(UTC) + timedelta(minutes=2)).isoformat())
            )
            if changed.rowcount != 1:
                raise RuntimeError("Lost collection lease")

    def checkpoint(jobs):
        heartbeat()
        with factory.begin() as db:
            db.get(Run, run_id).raw_checkpoint = [job.to_dict() for job in jobs]

    diagnostics: dict[str, Any]
    try:
        if mode == "offline":
            rows, diagnostics = (
                json.loads(FIXTURE.read_text()),
                {"demo": True, "requests": 0, "capped": False},
            )
        else:
            with LinkedInPublicClient(
                delay=1.5,
                max_requests=settings.request_cap,
                checkpoint=checkpoint,
                heartbeat=heartbeat,
            ) as client:
                try:
                    rows = [
                        j.to_dict()
                        for j in client.search(
                            [config.source(kind)],
                            limit_per_source=config.max_items,
                            global_limit=config.max_items,
                        )
                    ]
                finally:
                    with factory.begin() as db:
                        db.get(Run, run_id).diagnostics = asdict(client.diagnostics)
                diagnostics = asdict(client.diagnostics)
        heartbeat()
        # At most 100 bounded 45-second extraction calls; reserve an extraction lease.
        # Crashed workers are interrupted rather than repeating uncertain paid requests.
        with factory.begin() as db:
            db.get(Run, run_id).lease_until = (datetime.now(UTC) + timedelta(hours=2)).isoformat()
        with factory.begin() as db:
            run = db.get(Run, run_id)
            run.raw_checkpoint = rows
            counts = ingest(db, run, rows, settings)
            run.diagnostics = {**diagnostics, **counts}
            run.state = (
                "partial"
                if diagnostics.get("detail_failures")
                or counts["quarantined"]
                or any(
                    t in diagnostics.get("termination", []) for t in ["repeated_page", "no_new_ids"]
                )
                else "completed"
            )
            run.finished_at = now()
            logging.warning(
                "collection_finished run_id=%s state=%s counts=%s", run.id, run.state, counts
            )
        from market.analytics import materialize

        with factory.begin() as db:
            materialize(db, db.get(Run, run_id).query_version_id)
    except Exception as exc:
        with factory.begin() as db:
            run = db.get(Run, run_id)
            run.state, run.error, run.finished_at = "failed", type(exc).__name__, now()
            logging.error("collection_failed run_id=%s error_type=%s", run_id, type(exc).__name__)
    return True
