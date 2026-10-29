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
    parser.add_argument("command", choices=["seed", "worker", "replay", "schedule", "runs"])
    parser.add_argument(
        "--live", action="store_true", help="Allow the worker to use live collection"
    )
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--file", type=Path)
    parser.add_argument("--run-id")
    args = parser.parse_args()
    settings = Settings()
    if args.command == "worker" and settings.collection_mode == "live" and not args.live:
        parser.error("Live worker requires --live")
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
            work_once(Session, settings)
            if args.once:
                break
            time.sleep(10)
