import hashlib
import json
import logging
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from linkedin_jobs_cli.client import LinkedInClientError, LinkedInPublicClient
from linkedin_jobs_cli.models import JobPosting
from sqlalchemy import select, update

from market.contracts import MonitorConfig
from market.enrichment import check_source, enrich_jobs
from market.extraction import clean_description, extract, version
from market.models import (
    Budget,
    Compensation,
    DetailTask,
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
from market.taxonomy import ALIASES

FIXTURE = Path(__file__).resolve().parents[2] / "tests/fixtures/jobs.json"


def enqueue(db, monitor_id, settings, clock=None, kind="daily"):
    clock = clock or datetime.now(UTC)
    if settings.collection_mode == "live":
        check_source(db, clock)
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
    llm_reservation = 0  # Paid requests are reserved atomically by the extraction stage.
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
        existing_observation = db.scalar(
            select(Observation).where(
                Observation.run_id == run.id,
                Observation.job_id.in_(
                    select(SourceJob.id).where(
                        SourceJob.source == "linkedin", SourceJob.source_job_id == raw["id"]
                    )
                ),
            )
        )
        if existing_observation and not raw.get("descriptionText"):
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
        if existing_observation:
            existing_observation.snapshot_id = snapshot.id
        else:
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
                    "schedule_skipped monitor_id=%s reason=source_or_budget_unavailable", monitor.id
                )


def work_once(factory, settings, clock=None, *, allow_live=False):
    if settings.collection_mode == "live" and not allow_live:
        raise ValueError("Live execution requires explicit authorization")
    clock = clock or datetime.now(UTC)
    owner = uid()
    with factory.begin() as db:
        if settings.collection_mode == "live":
            try:
                check_source(db, clock)
            except ValueError:
                return False
        # Expired uncertain requests are never automatically repeated.
        for expired in db.scalars(
            select(Run)
            .where(
                Run.state == "running",
                Run.lease_until < clock.isoformat(),
                Run.mode == settings.collection_mode,
            )
            .with_for_update(skip_locked=True)
        ):
            expired.state, expired.error = (
                "interrupted",
                "Worker lease expired; replay checkpoint or explicitly run on another day",
            )
        run = db.scalar(
            select(Run)
            .where(Run.state == "queued", Run.mode == settings.collection_mode)
            .order_by(Run.created_at, Run.id)
            .with_for_update(skip_locked=True)
        )
        if not run and settings.collection_mode == "live":
            # Drain due missing-description tasks without issuing another search.
            for prior in db.scalars(
                select(Run)
                .where(Run.mode == "live", Run.state == "partial")
                .order_by(Run.created_at.desc())
                .limit(20)
            ):
                qv = db.get(QueryVersion, prior.query_version_id)
                monitor = db.get(Monitor, prior.monitor_id)
                if not monitor.enabled or monitor.current_version != qv.version:
                    continue
                ids = [
                    row.get("id") for row in prior.raw_checkpoint if not row.get("descriptionText")
                ]
                due = db.scalar(
                    select(DetailTask)
                    .where(
                        DetailTask.source_job_id.in_(ids),
                        DetailTask.state.in_(["pending", "retry"]),
                        DetailTask.attempts < settings.detail_max_attempts,
                        (
                            DetailTask.next_attempt_at.is_(None)
                            | (DetailTask.next_attempt_at <= clock.isoformat())
                        ),
                    )
                    .limit(1)
                )
                if due is None:
                    continue
                try:
                    with db.begin_nested():
                        candidate = enqueue(
                            db, prior.monitor_id, settings, clock, kind="manual-" + uid()
                        )
                        if candidate.id == prior.id or candidate.state != "queued":
                            continue
                        candidate.input = {
                            **candidate.input,
                            "resume_rows": prior.raw_checkpoint,
                            "listing_status": prior.diagnostics.get("listing_status", "unknown"),
                            "capped": prior.diagnostics.get("capped", True),
                        }
                        run = candidate
                except ValueError:
                    logging.warning("enrichment_skipped reason=budget_exhausted")
                break
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
    with factory() as db:
        run_input = db.get(Run, run_id).input
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

    def checkpoint_rows(rows):
        heartbeat()
        with factory.begin() as db:
            db.get(Run, run_id).raw_checkpoint = rows

    def checkpoint(jobs):
        checkpoint_rows([job.to_dict() for job in jobs])

    diagnostics: dict[str, Any]
    try:
        if mode == "offline":
            rows, diagnostics = (
                json.loads(FIXTURE.read_text()),
                {
                    "demo": True,
                    "requests": 0,
                    "capped": False,
                    "listing_status": "completed",
                    "enrichment_status": "completed",
                },
            )
        else:
            with LinkedInPublicClient(
                delay=1.5,
                max_requests=settings.request_cap,
                checkpoint=checkpoint,
                heartbeat=heartbeat,
            ) as client:
                try:
                    if run_input.get("resume_rows"):
                        jobs = [
                            JobPosting(
                                id=r["id"],
                                link=r.get("link")
                                or "https://www.linkedin.com/jobs/view/" + r["id"],
                                title=r["title"],
                                company_name=r.get("companyName") or "Unknown",
                                location=r.get("location") or "Unknown",
                                posted_at=r.get("postedAt"),
                                salary_info=r.get("salaryInfo") or [],
                            )
                            for r in run_input["resume_rows"]
                        ]
                        client.diagnostics.listing_status = run_input.get(
                            "listing_status", "unknown"
                        )
                        client.diagnostics.capped = run_input.get("capped", True)
                    else:
                        jobs = client.search(
                            [config.source(kind)],
                            limit_per_source=config.max_items,
                            global_limit=config.max_items,
                            include_details=False,
                        )
                    # Commit listing completeness before independently enriching descriptions.
                    with factory.begin() as db:
                        db.get(Run, run_id).diagnostics = asdict(client.diagnostics)
                    listing_rows = [j.to_dict() for j in jobs]

                    def save_enrichment(enriched):
                        # Preserve unprocessed listing records if the worker exits mid-enrichment.
                        checkpoint_rows(enriched + listing_rows[len(enriched) :])

                    rows = enrich_jobs(factory, client, jobs, settings, save_enrichment)
                    client.diagnostics.enrichment_status = (
                        "partial" if any(r.get("detailError") for r in rows) else "completed"
                    )
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
                if diagnostics.get("listing_status") != "completed"
                or any(r.get("detailError") for r in rows)
                or diagnostics.get("detail_failures")
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

        if (
            mode == "live"
            and settings.llm_mode == "live"
            and settings.llm_api_key
            and settings.llm_model
        ):
            from market.processing import process_saved

            process_saved(
                factory, settings, limit=config.max_items, run_id=run_id, allow_live=allow_live
            )
        else:
            from market.observability import event

            event(
                "ai.processing.skipped",
                run_id=run_id,
                mode=settings.llm_mode,
                reason="offline_collection"
                if mode == "offline"
                else "rules_mode"
                if settings.llm_mode == "rules"
                else "model_or_key_unconfigured",
                key_configured=bool(settings.llm_api_key),
                model_configured=bool(settings.llm_model),
                actual_calls=0,
            )

        with factory.begin() as db:
            materialize(db, db.get(Run, run_id).query_version_id)
    except Exception as exc:
        with factory.begin() as db:
            run = db.get(Run, run_id)
            if run.raw_checkpoint:
                # A later page failure must not discard valid earlier listings.
                saved_settings = settings.model_copy(update={"llm_mode": "rules"})
                counts = ingest(db, run, run.raw_checkpoint, saved_settings)
                run.diagnostics = {
                    **run.diagnostics,
                    **counts,
                    "listing_status": "incomplete",
                    "enrichment_status": "deferred",
                }
            if isinstance(exc, LinkedInClientError) and exc.code in {
                "access_challenge",
                "rate_limited",
            }:
                from market.enrichment import source_health

                health = source_health(db)
                health.state = "paused" if exc.code == "access_challenge" else "cooldown"
                health.reason, health.retry_at, health.updated_at = exc.code, exc.retry_at, now()
            run.state, run.error, run.finished_at = (
                "partial" if run.raw_checkpoint else "failed",
                getattr(exc, "code", type(exc).__name__),
                now(),
            )
            logging.error("collection_failed run_id=%s error_type=%s", run_id, type(exc).__name__)
    return True
