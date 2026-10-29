import logging

import httpx
import pytest

from market import extraction
from market.analytics import compute
from market.contracts import JobExtraction, Requirement
from market.observability import JsonFormatter, audit_context, event
from market.pipeline import enqueue, ingest
from market.processing import process_rules_saved, process_saved
from market.taxonomy import classify_skill


def test_ai_and_api_capabilities_have_separate_subthemes():
    text = """Required Skills
Experience with LLMs, RAG, GitHub Copilot and API design.
Preferred Skills
Familiarity with Claude Code and OpenAPI.
Job Responsibilities
Use AI-assisted software development tools and validate AI-generated outputs.
"""
    result = extraction.rules({"title": "Engineer"}, text)
    by_name = {r.canonical_candidate: r for r in result.requirements}
    assert by_name["Large language models"].theme == "ai_engineering"
    assert by_name["GitHub Copilot"].subtheme == "ai_assisted_development"
    assert by_name["Retrieval-augmented generation"].subtheme == "applied_ai"
    assert by_name["API design"].theme == "api_and_system_design"
    assert by_name["OpenAPI"].theme == "api_and_system_design"
    assert by_name["Claude Code"].requiredness == "preferred"
    assert by_name["AI-assisted development"].context == "responsibility"
    assert by_name["AI-assisted development"].requiredness == "mentioned"
    assert (
        "Artificial intelligence" not in by_name
    )  # no duplicate generic AI inside a specific phrase


def test_taxonomy_keeps_distinct_languages_and_ignores_company_boilerplate():
    text = "The Company\nWe are an AI company.\nRequirements\nStrong C# and .NET experience; API designs and REST APIs."
    result = extraction.rules({"title": "Engineer"}, text)
    names = {r.canonical_candidate for r in result.requirements}
    assert {"C#", ".NET", "API design", "REST APIs"} <= names
    assert "Artificial intelligence" not in names
    assert classify_skill("AI tooling", "AI tooling", "practice")[2] == "ai_engineering"


def test_theme_counts_are_unique_jobs_and_contexts_remain_separate(context, clock, client):
    factory, settings, mid, qid = context
    with factory.begin() as db:
        run = enqueue(db, mid, settings, clock)
        ingest(
            db,
            run,
            [
                {
                    "id": "1",
                    "title": "Engineer",
                    "companyName": "A",
                    "descriptionText": "Required Skills\nExperience with GitHub Copilot, LLMs and API design.",
                },
                {
                    "id": "2",
                    "title": "Engineer",
                    "companyName": "B",
                    "descriptionText": "Job Responsibilities\nUse AI-assisted development and Claude Code.",
                },
            ],
            settings,
            clock,
        )
        run.state = "completed"
        result = compute(db, qid, clock.date(), clock.date())
        ai = next(t for t in result["themes"] if t["theme"] == "ai_engineering")
        assert ai["count"] == 2 and ai["denominator"] == 2
        assert ai["requirement_count"] == 1 and ai["responsibility_count"] == 1
        required = compute(db, qid, clock.date(), clock.date(), requiredness="required")
        assert all(s["context"] == "candidate_requirement" for s in required["skills"])
    response = client.get(
        f"/api/v1/monitors/{mid}/skills?from=2026-10-29&to=2026-10-29&theme=ai_engineering"
    ).json()
    assert response["data"] and all(s["theme"] == "ai_engineering" for s in response["data"])
    assert response["themes"][0]["count"] == 2
    assert client.get(f"/api/v1/monitors/{mid}/skills?theme=bogus").status_code == 422


def provider(monkeypatch, handler):
    original = httpx.Client
    monkeypatch.setattr(
        extraction.httpx,
        "Client",
        lambda **kwargs: original(transport=httpx.MockTransport(handler), **kwargs),
    )


def test_ai_call_events_have_correlation_usage_and_no_text_or_secrets(context, monkeypatch, caplog):
    secret = "sk-super-secret"
    text = "Requirements\nExperience with GitHub Copilot."
    result = JobExtraction(
        requirements=[
            Requirement(
                canonical_candidate="GitHub Copilot",
                surface_text="GitHub Copilot",
                category="tool",
                context="candidate_requirement",
                requiredness="required",
                evidence_quote="Experience with GitHub Copilot.",
                confidence=0.9,
            )
        ]
    )
    request_ids = []

    def handler(request):
        request_ids.append(request.headers["X-Client-Request-Id"])
        return httpx.Response(
            200,
            headers={"x-request-id": "req_safe123"},
            json={
                "id": "chatcmpl_safe",
                "choices": [
                    {"finish_reason": "stop", "message": {"content": result.model_dump_json()}}
                ],
                "usage": {"prompt_tokens": 800, "completion_tokens": 150, "total_tokens": 950},
            },
        )

    provider(monkeypatch, handler)
    settings = context[1].model_copy(
        update={
            "llm_mode": "live",
            "llm_api_key": secret,
            "llm_model": "fake-model",
            "llm_endpoint": "https://api.openai.com/v1/chat/completions?secret=do-not-log",
        }
    )
    with (
        caplog.at_level(logging.INFO, logger="market.ai"),
        audit_context(snapshot_id="snapshot123", extraction_id="extract123"),
    ):
        parsed, metadata = extraction.parse_with_llm(
            {"id": "123", "title": "Engineer", "descriptionText": text}, settings
        )
    records = [r for r in caplog.records if r.name == "market.ai"]
    assert [r.message for r in records] == [
        "ai.request.started",
        "ai.response.received",
        "ai.request.completed",
    ]
    assert all(r.audit["call_id"] == request_ids[0] for r in records)
    assert all(r.audit["snapshot_id"] == "snapshot123" for r in records)
    assert metadata["provider_request_id"] == "req_safe123"
    assert metadata["usage"]["total_tokens"] == 950
    assert parsed.requirements[0].theme == "ai_engineering"
    logs = "\n".join(JsonFormatter().format(r) for r in records)
    assert secret not in logs and text not in logs and "do-not-log" not in logs
    assert records[-1].audit["validation_status"] == "passed"
    assert records[0].audit["tools_enabled"] is False


@pytest.mark.parametrize("kind", ["http", "evidence"])
def test_ai_failure_events_explain_stage_without_response_body(context, monkeypatch, caplog, kind):
    def handler(_):
        if kind == "http":
            return httpx.Response(
                429,
                text="sensitive provider response sk-secret",
                headers={"x-request-id": "req_limited"},
            )
        result = JobExtraction(
            requirements=[
                Requirement(
                    canonical_candidate="Python",
                    surface_text="Python",
                    category="language",
                    context="candidate_requirement",
                    requiredness="required",
                    evidence_quote="invented evidence sk-secret",
                    confidence=0.9,
                )
            ]
        )
        return httpx.Response(
            200,
            json={
                "choices": [
                    {"finish_reason": "stop", "message": {"content": result.model_dump_json()}}
                ]
            },
        )

    provider(monkeypatch, handler)
    settings = context[1].model_copy(
        update={"llm_mode": "live", "llm_api_key": "sk-secret", "llm_model": "fake"}
    )
    with caplog.at_level(logging.INFO, logger="market.ai"), pytest.raises(Exception):
        extraction.parse_with_llm(
            {"title": "Engineer", "descriptionText": "Required Python experience."}, settings
        )
    failed = next(r for r in caplog.records if r.message == "ai.request.failed")
    assert failed.audit["failure_code"] == (
        "http_error" if kind == "http" else "unsupported_evidence"
    )
    assert failed.audit["failure_stage"] == (
        "response" if kind == "http" else "evidence_validation"
    )
    assert "sk-secret" not in JsonFormatter().format(failed)


def test_preview_and_rules_reprocessing_report_zero_ai_calls(context, clock, caplog):
    factory, settings, mid, _ = context
    with factory.begin() as db:
        run = enqueue(db, mid, settings.model_copy(update={"collection_mode": "live"}), clock)
        ingest(
            db,
            run,
            [
                {
                    "id": "1",
                    "title": "Engineer",
                    "companyName": "A",
                    "descriptionText": "Requirements\nExperience with GitHub Copilot and API design.",
                }
            ],
            settings,
            clock,
        )
        run.state = "completed"
    with caplog.at_level(logging.INFO, logger="market.ai"):
        process_saved(factory, settings, limit=1)
        process_rules_saved(factory, settings, limit=1)
    assert not any(r.message == "ai.request.started" for r in caplog.records)
    batches = [r for r in caplog.records if r.message == "ai.batch.completed"]
    assert batches and all(r.audit["actual_calls"] == 0 for r in batches)


def test_log_event_allowlist_and_identifier_redaction(caplog):
    with caplog.at_level(logging.INFO, logger="market.ai"):
        event(
            "ai.test",
            model="sk-secret",
            description="private job ad",
            headers={"Authorization": "secret"},
            request_body="secret",
        )
    record = caplog.records[-1]
    encoded = JsonFormatter().format(record)
    assert record.audit == {"model": "<redacted>"}
    assert "private job ad" not in encoded and "Authorization" not in encoded


def test_valid_llm_result_is_preserved_when_rules_are_refreshed(context, clock, client):
    from sqlalchemy import select

    from market.contracts import JobExtraction
    from market.models import Extraction

    factory, settings, mid, _ = context
    with factory.begin() as db:
        run = enqueue(db, mid, settings, clock)
        ingest(
            db,
            run,
            [
                {
                    "id": "1",
                    "title": "Engineer",
                    "companyName": "A",
                    "descriptionText": "Required Python experience.",
                }
            ],
            settings,
            clock,
        )
        original = db.scalar(select(Extraction))
        db.add(
            Extraction(
                snapshot_id=original.snapshot_id,
                version="llm:previous",
                status="valid",
                result=JobExtraction(seniority="senior").model_dump(mode="json"),
                created_at="2020-01-01T00:00:00+00:00",
            )
        )
    body = client.get("/api/v1/jobs/linkedin/1").json()
    assert body["extraction_version"] == "llm:previous"
    assert body["extraction"]["seniority"] == "senior"
