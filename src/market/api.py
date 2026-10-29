import hmac
from datetime import date, datetime, timedelta
from typing import Annotated, Literal
from zoneinfo import ZoneInfo

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import case, select, text
from sqlalchemy.exc import IntegrityError

from market.analytics import cohort, compute, observation_payload
from market.config import Settings
from market.contracts import MonitorConfig
from market.db import Session
from market.models import Extraction, Monitor, QueryVersion, Run, Snapshot, SourceHealth, SourceJob
from market.pipeline import enqueue
from market.taxonomy import THEMES, themed_result


def create_app(factory=Session, settings=None):
    settings = settings or Settings()
    from market.observability import configure_logging

    configure_logging(settings)
    app = FastAPI(title="Job Market Intelligence", version="0.1.0")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[settings.web_origin],
        allow_methods=["GET", "POST", "PATCH"],
        allow_headers=["Content-Type", "X-Admin-Key"],
    )

    def session():
        with factory.begin() as db:
            yield db

    def auth(request: Request, x_admin_key: Annotated[str | None, Header()] = None):
        if settings.admin_key:
            if not x_admin_key or not hmac.compare_digest(x_admin_key, settings.admin_key):
                raise HTTPException(401, "Admin key required")
        elif request.client and request.client.host not in {"127.0.0.1", "::1", "testclient"}:
            raise HTTPException(403, "Local mode accepts loopback clients only")
        if request.method in {"POST", "PATCH"} and request.headers.get("origin") not in {
            None,
            settings.web_origin,
        }:
            raise HTTPException(403, "Untrusted origin")
        host = request.headers.get("host", "").split(":")[0]
        if settings.app_mode == "local" and host not in {
            "localhost",
            "127.0.0.1",
            "[",
            "testserver",
        }:
            raise HTTPException(403, "Invalid local host")

    def current(db, mid):
        monitor = db.get(Monitor, mid)
        if not monitor:
            raise HTTPException(404, "Monitor not found")
        qv = db.scalar(
            select(QueryVersion).where(
                QueryVersion.monitor_id == mid, QueryVersion.version == monitor.current_version
            )
        )
        return monitor, qv

    def monitor_data(db, m):
        _, v = current(db, m.id)
        return {"id": m.id, "version": m.current_version, "query_version_id": v.id, **v.config}

    @app.get("/api/v1/health/live")
    def live():
        return {"status": "ok"}

    @app.get("/api/v1/health/ready")
    def ready(db=Depends(session)):
        try:
            if db.scalar(text("SELECT version_num FROM alembic_version")) != "0003":
                raise ValueError("Migration revision is not current")
        except Exception:
            raise HTTPException(503, "Database unavailable or migrations missing")
        return {"status": "ready"}

    def health_data(db):
        health = db.get(SourceHealth, "linkedin") if settings.collection_mode == "live" else None
        return {
            "source": "linkedin",
            "auth_mode": "guest",
            "state": health.state if health else "available",
            "reason": health.reason if health else None,
            "retry_at": health.retry_at if health else None,
        }

    @app.get("/api/v1/source-health", dependencies=[Depends(auth)])
    def get_source_health(db=Depends(session)):
        return health_data(db)

    @app.get("/api/v1/monitors", dependencies=[Depends(auth)])
    def monitors(db=Depends(session)):
        return {
            "data": [
                monitor_data(db, m)
                for m in db.scalars(select(Monitor).order_by(Monitor.created_at))
            ],
            "meta": {"demo": settings.collection_mode == "offline"},
        }

    @app.post("/api/v1/monitors", status_code=201, dependencies=[Depends(auth)])
    def create(config: MonitorConfig, db=Depends(session)):
        monitor = Monitor(name=config.name, enabled=config.enabled)
        db.add(monitor)
        db.flush()
        db.add(QueryVersion(monitor_id=monitor.id, version=1, config=config.model_dump()))
        db.flush()
        return monitor_data(db, monitor)

    @app.get("/api/v1/monitors/{mid}", dependencies=[Depends(auth)])
    def get_monitor(mid: str, db=Depends(session)):
        return monitor_data(db, current(db, mid)[0])

    @app.patch("/api/v1/monitors/{mid}", dependencies=[Depends(auth)])
    def edit(mid: str, config: MonitorConfig, db=Depends(session)):
        monitor = db.scalar(select(Monitor).where(Monitor.id == mid).with_for_update())
        if not monitor:
            raise HTTPException(404, "Monitor not found")
        monitor.current_version += 1
        monitor.name, monitor.enabled = config.name, config.enabled
        db.add(
            QueryVersion(
                monitor_id=mid, version=monitor.current_version, config=config.model_dump()
            )
        )
        db.flush()
        return monitor_data(db, monitor)

    @app.post("/api/v1/monitors/{mid}/refresh", status_code=202, dependencies=[Depends(auth)])
    def refresh(mid: str, db=Depends(session)):
        try:
            with db.begin_nested():
                run = enqueue(db, mid, settings)
        except ValueError as exc:
            raise HTTPException(409, str(exc))
        except IntegrityError:
            raise HTTPException(409, "Run already queued; reload runs")
        return {"run_id": run.id, "state": run.state, "mode": run.mode}

    @app.get("/api/v1/monitors/{mid}/runs", dependencies=[Depends(auth)])
    def runs(mid: str, db=Depends(session)):
        current(db, mid)
        rows = db.scalars(
            select(Run).where(Run.monitor_id == mid).order_by(Run.created_at.desc()).limit(100)
        )
        return {
            "data": [
                {
                    "id": r.id,
                    "query_version_id": r.query_version_id,
                    "date": r.logical_date,
                    "kind": r.kind,
                    "state": r.state,
                    "mode": r.mode,
                    "diagnostics": r.diagnostics,
                    "error": r.error,
                    "created_at": r.created_at,
                    "finished_at": r.finished_at,
                }
                for r in rows
            ]
        }

    @app.get("/api/v1/monitors/{mid}/jobs", dependencies=[Depends(auth)])
    def jobs(
        mid: str,
        cursor: str = "",
        limit: int = Query(25, ge=1, le=100),
        search: str = Query("", max_length=200),
        from_: Annotated[date | None, Query(alias="from")] = None,
        to: date | None = None,
        db=Depends(session),
    ):
        _, qv = current(db, mid)
        to = to or datetime.now(ZoneInfo(qv.config["timezone"])).date()
        from_ = from_ or to - timedelta(days=27)
        if from_ > to or (to - from_).days > 366:
            raise HTTPException(422, "Invalid window; maximum 366 days")
        rows, _, _, _, _ = cohort(
            db, qv.id, from_, to, mode=settings.collection_mode, states=("completed", "partial")
        )
        selected = sorted(
            [
                (observation, job, snapshot)
                for observation, job, snapshot in rows
                if job.source_job_id > cursor
                and search.casefold() in f"{job.title} {job.company} {job.location}".casefold()
            ],
            key=lambda row: row[1].source_job_id,
        )
        page = selected[:limit]
        return {
            "data": [
                {
                    "id": j.source_job_id,
                    "title": j.title,
                    "company": j.company,
                    "location": j.location,
                    "url": j.url,
                    "first_seen_at": j.first_seen_at,
                    "last_seen_at": j.last_seen_at,
                    "posted_at": j.posted_at,
                    "collection_state": db.get(Run, observation.run_id).state,
                    "description_available": bool(snapshot.description),
                    "description_cached": bool(
                        observation_payload(db, observation, snapshot).get("descriptionCached")
                    ),
                    "description_fetched_at": observation_payload(db, observation, snapshot).get(
                        "descriptionFetchedAt"
                    ),
                    "detail_error": observation_payload(db, observation, snapshot).get(
                        "detailError"
                    ),
                }
                for observation, j, snapshot in page
            ],
            "next_cursor": page[-1][1].source_job_id if len(selected) > limit else None,
            "meta": {"includes_partial": True, "total": len(rows)},
        }

    @app.get("/api/v1/jobs/{source}/{job_id}", dependencies=[Depends(auth)])
    def detail(source: str, job_id: str, include_source: bool = False, db=Depends(session)):
        job = db.scalar(
            select(SourceJob).where(SourceJob.source == source, SourceJob.source_job_id == job_id)
        )
        if not job:
            raise HTTPException(404, "Job not found")
        snapshot = db.scalar(
            select(Snapshot).where(Snapshot.job_id == job.id).order_by(Snapshot.observed_at.desc())
        )
        extraction = db.scalar(
            select(Extraction)
            .where(Extraction.snapshot_id == snapshot.id, Extraction.status == "valid")
            .order_by(
                case((Extraction.version.like("llm:%"), 0), else_=1),
                Extraction.created_at.desc(),
                Extraction.id.desc(),
            )
        )
        latest_attempt = db.scalar(
            select(Extraction)
            .where(Extraction.snapshot_id == snapshot.id)
            .order_by(Extraction.created_at.desc(), Extraction.id.desc())
        )
        latest_ai_attempt = db.scalar(
            select(Extraction)
            .where(Extraction.snapshot_id == snapshot.id, Extraction.version.like("llm:%"))
            .order_by(Extraction.created_at.desc(), Extraction.id.desc())
        )
        latest_attempt = latest_ai_attempt or latest_attempt
        return {
            "id": job.source_job_id,
            "title": snapshot.raw.get("title"),
            "description": snapshot.description if include_source else None,
            "source_available": bool(snapshot.description),
            "extraction": themed_result(extraction.result) if extraction else None,
            "extraction_status": extraction.status if extraction else "missing",
            "extraction_version": extraction.version if extraction else None,
            "processing_status": latest_attempt.status if latest_attempt else "missing",
            "processing_diagnostics": latest_attempt.diagnostics if latest_attempt else {},
            "selection": extraction.diagnostics if extraction else {},
        }

    @app.get("/api/v1/monitors/{mid}/{view}", dependencies=[Depends(auth)])
    def metrics(
        mid: str,
        view: Literal["overview", "skills", "titles", "salaries", "insights"],
        from_: Annotated[date | None, Query(alias="from")] = None,
        to: date | None = None,
        requiredness: Literal["required", "preferred", "familiarity", "mentioned", "unclear"]
        | None = None,
        currency: Literal["GBP", "USD", "EUR"] | None = None,
        theme: str | None = None,
        db=Depends(session),
    ):
        if from_ and to and (from_ > to or (to - from_).days > 366):
            raise HTTPException(422, "Invalid window; maximum 366 days")
        _, qv = current(db, mid)
        to = to or datetime.now(ZoneInfo(qv.config["timezone"])).date()
        from_ = from_ or to - timedelta(days=27)
        if from_ > to or (to - from_).days > 366:
            raise HTTPException(422, "Invalid window; maximum 366 days")
        if theme is not None and theme not in THEMES:
            raise HTTPException(422, "Unknown skill theme")
        result = compute(db, qv.id, from_, to, settings.collection_mode, requiredness)
        data = result[view]
        if view == "salaries" and currency:
            data["groups"] = [g for g in data["groups"] if g["currency"] == currency]
        if view == "skills" and theme:
            data = [item for item in data if item["theme"] == theme]
        response = {"data": data, "meta": {**result["meta"], "source_health": health_data(db)}}
        if view == "skills":
            response["themes"] = [
                item for item in result["themes"] if not theme or item["theme"] == theme
            ]
        return response

    return app


app = create_app()
