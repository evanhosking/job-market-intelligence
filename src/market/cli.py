import argparse
import json
import time
from pathlib import Path

from sqlalchemy import select

from market.analytics import materialize
from market.config import Settings
from market.contracts import MonitorConfig
from market.db import Session
from market.models import Monitor, QueryVersion, Run
from market.pipeline import enqueue, ingest, schedule, work_once


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "command",
        choices=[
            "seed",
            "worker",
            "replay",
            "schedule",
            "runs",
            "refresh",
            "retry-details",
            "source-resume",
            "extract",
        ],
    )
    parser.add_argument(
        "--live", action="store_true", help="Allow the worker to use live collection"
    )
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--file", type=Path)
    parser.add_argument("--run-id")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--limit", type=int, default=50)
    args = parser.parse_args()
    settings = Settings()
    if args.command == "extract":
        from market.processing import process_saved

        try:
            result = process_saved(
                Session,
                settings,
                limit=args.limit,
                run_id=args.run_id,
                allow_live=args.live and not args.dry_run,
            )
        except ValueError as exc:
            parser.error(str(exc))
        if args.live and not args.dry_run:
            with Session.begin() as db:
                for processed_version in db.scalars(select(QueryVersion)):
                    materialize(db, processed_version.id)
        print(json.dumps(result))
        return
    if args.command == "worker" and settings.collection_mode == "live" and not args.live:
        parser.error("Live worker requires --live")
    if args.command in {"refresh", "retry-details", "source-resume"}:
        if not args.live or settings.collection_mode != "live":
            parser.error("This operation requires COLLECTION_MODE=live and --live")
        from market.enrichment import resume_source
        from market.models import uid

        if args.command == "source-resume":
            with Session.begin() as db:
                resume_source(db)
            print("Guest source resumed explicitly; no login or credentials were stored")
            return
        if not 1 <= args.limit <= 100:
            parser.error("--limit must be between 1 and 100")
        with Session.begin() as db:
            previous = db.get(Run, args.run_id) if args.run_id else None
            if args.command == "retry-details" and (
                not previous or previous.mode != "live" or not previous.raw_checkpoint
            ):
                parser.error("retry-details requires --run-id for a saved live run")
            monitor = (
                db.get(Monitor, previous.monitor_id)
                if previous
                else db.scalar(select(Monitor).where(Monitor.enabled.is_(True)))
            )
            if not monitor:
                parser.error("No enabled monitor")
            if previous and previous.state in {"queued", "running"}:
                parser.error("Cannot retry an active run")
            qv = db.scalar(
                select(QueryVersion).where(
                    QueryVersion.monitor_id == monitor.id,
                    QueryVersion.version == monitor.current_version,
                )
            )
            if qv is None:
                parser.error("Monitor has no current query version")
            limit = max(args.limit, len(previous.raw_checkpoint)) if previous else args.limit
            config = MonitorConfig.model_validate({**qv.config, "max_items": limit})
            if config.model_dump() != qv.config:
                monitor.current_version += 1
                qv = QueryVersion(
                    monitor_id=monitor.id,
                    version=monitor.current_version,
                    config=config.model_dump(),
                )
                db.add(qv)
                db.flush()
            run = enqueue(db, monitor.id, settings, kind="manual-" + uid())
            if previous:
                run.input = {
                    **run.input,
                    "resume_rows": previous.raw_checkpoint,
                    "listing_status": previous.diagnostics.get("listing_status", "unknown"),
                    "capped": previous.diagnostics.get("capped", True),
                }
            print(run.id)
        return
    if args.command == "seed":
        with Session.begin() as db:
            monitor = db.scalar(select(Monitor))
            if not monitor:
                config = MonitorConfig(name="Software Engineer")
                monitor = Monitor(name=config.name)
                db.add(monitor)
                db.flush()
                db.add(QueryVersion(monitor_id=monitor.id, version=1, config=config.model_dump()))
                db.flush()
            print(monitor.id)
    elif args.command == "replay":
        # Replay is deterministic and never repeats paid extraction calls.
        settings = settings.model_copy(update={"llm_mode": "rules"})
        with Session.begin() as db:
            monitor = db.scalar(select(Monitor))
            if not monitor:
                parser.error("Run market seed first")
            run = db.get(Run, args.run_id) if args.run_id else enqueue(db, monitor.id, settings)
            if not run:
                parser.error("Run not found")
            if run.state == "running":
                parser.error("Cannot replay a run with an active worker")
            if args.file:
                rows = json.loads(args.file.read_text())
            else:
                rows = run.raw_checkpoint
            if not rows:
                parser.error("Provide --file or a run with saved checkpoint records")
            counts = ingest(db, run, rows, settings)
            run.raw_checkpoint = rows
            # Replay of an interrupted live download cannot certify completeness.
            run.state = "partial" if run.mode == "live" or counts["quarantined"] else "completed"
            run.diagnostics = {
                **run.diagnostics,
                **counts,
                "replayed": True,
                "demo": run.mode == "offline",
            }
            from market.models import now

            run.finished_at = now()
            materialize(db, run.query_version_id)
            print(json.dumps({"run_id": run.id, **counts}))
    elif args.command == "runs":
        with Session() as db:
            for run in db.scalars(select(Run).order_by(Run.created_at.desc()).limit(20)):
                print(run.id, run.state, run.mode, run.diagnostics)
    else:
        while True:
            with Session.begin() as db:
                schedule(db, settings)
            if args.command == "schedule":
                break
            work_once(Session, settings, allow_live=args.live)
            if args.once:
                break
            time.sleep(10)
