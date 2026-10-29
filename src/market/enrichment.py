"""Guest-only description retrieval. Persistent cache, bounded attempts and source pauses."""

from datetime import UTC, datetime, timedelta

from linkedin_jobs_cli.client import LinkedInClientError
from sqlalchemy import select

from market.models import DetailTask, Run, Snapshot, SourceHealth, SourceJob, now


def source_health(db):
    health = db.get(SourceHealth, "linkedin")
    if health is None:
        health = SourceHealth(source="linkedin", state="available")
        db.add(health)
        db.flush()
    return health


def check_source(db, clock=None):
    stamp = (clock or datetime.now(UTC)).isoformat()
    health = source_health(db)
    if health.state == "cooldown" and health.retry_at and health.retry_at <= stamp:
        health.state, health.reason, health.retry_at = "available", None, None
    if health.state != "available":
        raise ValueError("Source access unavailable: " + (health.reason or health.state))


def resume_source(db):
    health = source_health(db)
    health.state, health.reason, health.retry_at, health.updated_at = "available", None, None, now()
    for task in db.scalars(select(DetailTask).where(DetailTask.state == "access_blocked")):
        task.state, task.next_attempt_at, task.attempts = "pending", None, 0


def task_for(db, job_id):
    task = db.scalar(select(DetailTask).where(DetailTask.source_job_id == job_id).with_for_update())
    if task is None:
        task = DetailTask(source_job_id=job_id, state="pending", attempts=0, payload={})
        # Bootstrap only from an actual live snapshot, never the demo dataset.
        snapshot = db.scalar(
            select(Snapshot)
            .join(SourceJob, SourceJob.id == Snapshot.job_id)
            .join(Run, Run.id == Snapshot.run_id)
            .where(
                SourceJob.source == "linkedin",
                SourceJob.source_job_id == job_id,
                Snapshot.description != "",
                Run.mode == "live",
            )
            .order_by(Snapshot.observed_at.desc())
        )
        if snapshot:
            task.payload, task.fetched_at, task.state = snapshot.raw, snapshot.observed_at, "ready"
        db.add(task)
        db.flush()
    return task


def with_cached_description(raw, payload, fetched_at, *, stale=False):
    merged = dict(raw)
    for key in (
        "descriptionText",
        "descriptionHtml",
        "seniorityLevel",
        "employmentType",
        "industries",
        "salaryInfo",
    ):
        if payload.get(key):
            merged[key] = payload[key]
    merged["descriptionFetchedAt"] = fetched_at
    merged["descriptionCached"] = True
    merged["descriptionStale"] = stale
    return merged


def enrich_jobs(factory, client, jobs, settings, save_checkpoint):
    rows = []
    for job in jobs:
        raw = job.to_dict()
        clock = datetime.now(UTC)
        with factory.begin() as db:
            task = task_for(db, job.id)
            payload, fetched_at = task.payload, task.fetched_at
            fresh = bool(
                payload.get("descriptionText")
                and fetched_at
                and clock - datetime.fromisoformat(fetched_at)
                < timedelta(hours=settings.detail_cache_hours)
            )
            if fresh:
                rows.append(with_cached_description(raw, payload, fetched_at))
                save = True
            else:
                save = False
            health = source_health(db)
            blocked = health.state != "available"
            deferred = task.next_attempt_at and task.next_attempt_at > clock.isoformat()
            exhausted = task.attempts >= settings.detail_max_attempts or task.state == "unavailable"
            if not fresh and not blocked and not deferred and not exhausted:
                task.attempts += 1
                task.state = "running"
                task.next_attempt_at = (clock + timedelta(minutes=2)).isoformat()
        if save:
            save_checkpoint(rows)
            continue
        if blocked or deferred or exhausted:
            raw["detailError"] = (
                "source_access_unavailable"
                if blocked
                else "retry_deferred"
                if deferred
                else "attempt_limit"
            )
        else:
            try:
                client.fetch_detail(job)
                raw = job.to_dict()
                raw["descriptionFetchedAt"] = now()
                with factory.begin() as db:
                    task = db.get(DetailTask, job.id)
                    task.payload, task.fetched_at = raw, raw["descriptionFetchedAt"]
                    task.state, task.attempts, task.last_error, task.next_attempt_at = (
                        "ready",
                        0,
                        None,
                        None,
                    )
            except LinkedInClientError as error:
                raw["detailError"] = error.code
                with factory.begin() as db:
                    task = db.get(DetailTask, job.id)
                    task.last_error = error.code
                    task.state = (
                        "retry"
                        if error.code in {"transient", "rate_limited", "budget_exhausted"}
                        else "access_blocked"
                        if error.code == "access_challenge"
                        else "unavailable"
                    )
                    task.next_attempt_at = (
                        error.retry_at
                        or (clock + timedelta(minutes=15 * max(1, task.attempts))).isoformat()
                    )
                    if error.code in {"access_challenge", "rate_limited"}:
                        health = source_health(db)
                        health.state = "paused" if error.code == "access_challenge" else "cooldown"
                        health.reason, health.retry_at, health.updated_at = (
                            error.code,
                            error.retry_at,
                            now(),
                        )
                        client.diagnostics.access_paused = error.code == "access_challenge"
        if not raw.get("descriptionText") and payload.get("descriptionText"):
            raw = with_cached_description(raw, payload, fetched_at, stale=True)
        rows.append(raw)
        save_checkpoint(rows)
    return rows
