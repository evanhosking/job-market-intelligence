"""Reprocess saved live descriptions independently of scraping, with durable paid-call claims."""

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from market.extraction import ALIASES, clean_description, parse_with_llm, version
from market.models import (
    Budget,
    Compensation,
    Extraction,
    Observation,
    Run,
    Skill,
    SkillAlias,
    SkillMention,
    Snapshot,
)
from market.selection import select_excerpts, selection_metadata


def process_saved(factory, settings, limit=20, run_id=None, allow_live=False):
    if not 1 <= limit <= 100:
        raise ValueError("Extraction limit must be between 1 and 100")
    if allow_live and (
        settings.llm_mode != "live" or not settings.llm_api_key or not settings.llm_model
    ):
        raise ValueError(
            "Configure LLM_MODE=live, LLM_API_KEY and LLM_MODEL before live extraction"
        )
    result: dict[str, Any] = {
        "preview": not allow_live,
        "selected": 0,
        "processed": 0,
        "cached": 0,
        "quarantined": 0,
        "budget_exhausted": False,
        "items": [],
    }
    # Only descriptions actually observed in live collections can leave this application.
    with factory() as db:
        ids = (
            select(Observation.snapshot_id)
            .join(Run, Run.id == Observation.run_id)
            .where(Run.mode == "live")
        )
        if run_id:
            ids = ids.where(Run.id == run_id)
        snapshots = list(
            db.scalars(
                select(Snapshot)
                .where(Snapshot.id.in_(ids), Snapshot.description != "")
                .order_by(Snapshot.observed_at.desc(), Snapshot.id)
            )
        )
    attempted = 0
    for snapshot in snapshots:
        text = clean_description(snapshot.raw)
        excerpts = select_excerpts(text, settings.llm_input_char_cap)
        if not excerpts:
            continue
        metadata = selection_metadata(text, excerpts)
        if not allow_live:
            result["items"].append(
                {
                    "snapshot_id": snapshot.id,
                    "job_id": snapshot.raw.get("id"),
                    "source_chars": len(text),
                    "selected_chars": metadata["selected_chars"],
                    "excerpt_count": len(excerpts),
                    "sections": sorted({e.section for e in excerpts}),
                }
            )
            result["selected"] += 1
            if result["selected"] >= limit:
                break
            continue
        if attempted >= limit:
            break
        # Claim and reserve BEFORE the external call, independently of source ingestion.
        try:
            with factory.begin() as db:
                if db.scalar(
                    select(Extraction).where(
                        Extraction.snapshot_id == snapshot.id,
                        Extraction.version == version(settings),
                    )
                ):
                    result["cached"] += 1
                    continue
                month = datetime.now(UTC).strftime("%Y-%m")
                if db.bind.dialect.name == "postgresql":
                    from sqlalchemy import text as sql_text

                    db.execute(sql_text("SELECT pg_advisory_xact_lock(731901)"))
                if not db.get(Budget, month):
                    db.add(Budget(month=month, reserved=0, llm_reserved=0))
                    db.flush()
                reserved = db.execute(
                    update(Budget)
                    .where(
                        Budget.month == month,
                        Budget.llm_reserved < settings.llm_monthly_request_cap,
                    )
                    .values(llm_reserved=Budget.llm_reserved + 1)
                )
                if reserved.rowcount != 1:
                    result["budget_exhausted"] = True
                    break
                extraction = Extraction(
                    snapshot_id=snapshot.id,
                    version=version(settings),
                    status="processing",
                    result={},
                    diagnostics=metadata,
                )
                db.add(extraction)
                db.flush()
                eid = extraction.id
        except IntegrityError:
            result["cached"] += 1
            continue
        attempted += 1
        result["selected"] += 1
        try:
            parsed, diagnostics = parse_with_llm(snapshot.raw, settings)
            with factory.begin() as db:
                extraction = db.get(Extraction, eid)
                extraction.status, extraction.result, extraction.diagnostics = (
                    "valid",
                    parsed.model_dump(mode="json"),
                    diagnostics,
                )
                for ordinal, req in enumerate(parsed.requirements):
                    name = req.canonical_candidate
                    if not db.get(Skill, name):
                        db.add(Skill(id=name))
                        db.flush()
                        for alias in ALIASES.get(name, [name]):
                            if not db.get(SkillAlias, alias.casefold()):
                                db.add(SkillAlias(alias=alias.casefold(), skill_id=name))
                    db.add(
                        SkillMention(
                            extraction_id=eid,
                            ordinal=ordinal,
                            skill_id=name,
                            evidence=req.model_dump(mode="json"),
                        )
                    )
                for pay in parsed.compensation:
                    db.add(Compensation(extraction_id=eid, disclosure=pay.model_dump(mode="json")))
            result["processed"] += 1
        except Exception as exc:
            with factory.begin() as db:
                extraction = db.get(Extraction, eid)
                extraction.status, extraction.error = "quarantined", type(exc).__name__
            result["quarantined"] += 1
    return result
