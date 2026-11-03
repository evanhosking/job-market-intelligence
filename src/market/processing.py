"""Reprocess saved live descriptions independently of scraping, with durable paid-call claims."""

import logging
import time
from collections import Counter
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from market.extraction import clean_description, parse_with_llm, version
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
from market.observability import audit_context, event
from market.selection import select_excerpts, selection_metadata
from market.taxonomy import ALIASES, TAXONOMY_VERSION


def _process_saved(factory, settings, limit=20, run_id=None, allow_live=False):
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
        raw = {**snapshot.raw, "descriptionText": snapshot.description}
        with audit_context(
            snapshot_id=snapshot.id, job_id=str(snapshot.raw.get("id") or "unknown")
        ):
            text = clean_description(raw)
            excerpts = select_excerpts(text, settings.llm_input_char_cap)
            if not excerpts:
                event("ai.selection.skipped", reason="no_relevant_excerpts")
                continue
            metadata = selection_metadata(text, excerpts)
            event(
                "ai.selection.completed",
                source_chars=len(text),
                selected_chars=metadata["selected_chars"],
                excerpt_count=len(excerpts),
                sections=sorted({e.section for e in excerpts}),
                mode="live" if allow_live else "preview",
            )
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
                    cached = db.scalar(
                        select(Extraction).where(
                            Extraction.snapshot_id == snapshot.id,
                            Extraction.version == version(settings),
                        )
                    )
                    if cached:
                        event(
                            "ai.extraction.cached",
                            extraction_id=cached.id,
                            status=cached.status,
                            reason="attempt_already_recorded",
                        )
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
                        event(
                            "ai.budget.exhausted",
                            level=logging.WARNING,
                            month=month,
                            cap=settings.llm_monthly_request_cap,
                        )
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
                    event(
                        "ai.budget.reserved",
                        extraction_id=eid,
                        month=month,
                        reserved=db.get(Budget, month).llm_reserved,
                        cap=settings.llm_monthly_request_cap,
                    )
            except IntegrityError:
                event("ai.extraction.cached", reason="concurrent_claim")
                result["cached"] += 1
                continue
            attempted += 1
            result["selected"] += 1
            try:
                with audit_context(extraction_id=eid):
                    parsed, diagnostics = parse_with_llm(raw, settings)
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
                        db.add(
                            Compensation(extraction_id=eid, disclosure=pay.model_dump(mode="json"))
                        )
                event(
                    "ai.result.persisted",
                    extraction_id=eid,
                    status="valid",
                    requirement_count=len(parsed.requirements),
                    compensation_count=len(parsed.compensation),
                    theme_counts=dict(Counter(r.theme for r in parsed.requirements)),
                )
                result["processed"] += 1
            except Exception as exc:
                with factory.begin() as db:
                    extraction = db.get(Extraction, eid)
                    extraction.status, extraction.error = "quarantined", type(exc).__name__
                    extraction.diagnostics = {
                        **extraction.diagnostics,
                        **getattr(exc, "ai_diagnostics", {}),
                        "error_type": type(exc).__name__,
                    }
                event(
                    "ai.result.quarantined",
                    level=logging.WARNING,
                    extraction_id=eid,
                    error_type=type(exc).__name__,
                    reason="previous_valid_result_preserved",
                )
                result["quarantined"] += 1
    return result


def process_saved(factory, settings, limit=20, run_id=None, allow_live=False):
    started = time.perf_counter()
    with audit_context(batch_id=str(uuid4()), run_id=run_id):
        event(
            "ai.batch.started",
            mode="live" if allow_live else "preview",
            limit=limit,
            key_configured=bool(settings.llm_api_key),
            model_configured=bool(settings.llm_model),
            model=settings.llm_model or "unconfigured",
            taxonomy_version=TAXONOMY_VERSION,
        )
        try:
            result = _process_saved(factory, settings, limit, run_id, allow_live)
        except Exception as exc:
            event(
                "ai.batch.failed",
                level=logging.WARNING,
                error_type=type(exc).__name__,
                duration_ms=round((time.perf_counter() - started) * 1000),
            )
            raise
        event(
            "ai.batch.completed",
            **{k: v for k, v in result.items() if k != "items"},
            actual_calls=result["processed"] + result["quarantined"],
            duration_ms=round((time.perf_counter() - started) * 1000),
        )
        return result


def process_rules_saved(factory, settings, limit=20, run_id=None):
    """Refresh deterministic taxonomy on saved snapshots without altering source sightings."""
    from market.extraction import extract

    if not 1 <= limit <= 100:
        raise ValueError("Extraction limit must be between 1 and 100")
    settings = settings.model_copy(update={"llm_mode": "rules"})
    result = {"processed": 0, "cached": 0, "quarantined": 0}
    with audit_context(batch_id=str(uuid4()), run_id=run_id):
        event("ai.batch.started", mode="rules", limit=limit, taxonomy_version=TAXONOMY_VERSION)
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
        for snapshot in snapshots:
            if result["processed"] + result["quarantined"] >= limit:
                break
            with factory.begin() as db:
                if db.scalar(
                    select(Extraction).where(
                        Extraction.snapshot_id == snapshot.id,
                        Extraction.version == version(settings),
                    )
                ):
                    result["cached"] += 1
                    continue
                try:
                    parsed = extract(
                        {**snapshot.raw, "descriptionText": snapshot.description}, settings
                    )
                    extraction = Extraction(
                        snapshot_id=snapshot.id,
                        version=version(settings),
                        status="valid",
                        result=parsed.model_dump(mode="json"),
                        diagnostics={
                            "mode": "rules",
                            "taxonomy_version": TAXONOMY_VERSION,
                            "actual_calls": 0,
                        },
                    )
                    db.add(extraction)
                    db.flush()
                    for ordinal, req in enumerate(parsed.requirements):
                        name = req.canonical_candidate
                        if not db.get(Skill, name):
                            db.add(Skill(id=name, taxonomy_version=TAXONOMY_VERSION))
                            db.flush()
                        db.add(
                            SkillMention(
                                extraction_id=extraction.id,
                                ordinal=ordinal,
                                skill_id=name,
                                evidence=req.model_dump(mode="json"),
                            )
                        )
                    for pay in parsed.compensation:
                        db.add(
                            Compensation(
                                extraction_id=extraction.id, disclosure=pay.model_dump(mode="json")
                            )
                        )
                    event(
                        "ai.rules.completed",
                        snapshot_id=snapshot.id,
                        extraction_id=extraction.id,
                        requirement_count=len(parsed.requirements),
                        theme_counts=dict(Counter(r.theme for r in parsed.requirements)),
                        actual_calls=0,
                    )
                    result["processed"] += 1
                except (ValueError, TypeError) as exc:
                    db.add(
                        Extraction(
                            snapshot_id=snapshot.id,
                            version=version(settings),
                            status="quarantined",
                            result={},
                            error=type(exc).__name__,
                        )
                    )
                    result["quarantined"] += 1
        event("ai.batch.completed", mode="rules", actual_calls=0, **result)
    return result
