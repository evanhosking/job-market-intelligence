"""Structured AI audit events: metadata only, no prompts, adverts, headers or secrets."""

import contextvars
import json
import logging
import re
import sys
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Any

CONTEXT: contextvars.ContextVar[dict[str, Any]] = contextvars.ContextVar(
    "ai_log_context", default={}
)
LOGGER = logging.getLogger("market.ai")
FIELDS = set(
    "purpose input_sha256 output_token_cap tools_enabled structured_output batch_id run_id job_id snapshot_id extraction_id call_id model mode limit key_configured model_configured endpoint_host prompt_version selector_version taxonomy_version source_chars selected_chars excerpt_count sections reserved cap month duration_ms http_status provider_request_id provider_response_id finish_reason usage validation_status requirement_count compensation_count theme_counts error_type failure_code failure_stage status reason processed selected cached quarantined preview budget_exhausted actual_calls".split()
)


class JsonFormatter(logging.Formatter):
    def format(self, record):
        data = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "event": record.getMessage(),
        }
        data.update(getattr(record, "audit", {}))
        return json.dumps(data, ensure_ascii=False, default=str)


def configure_logging(settings):
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(
        JsonFormatter()
        if settings.log_format == "json"
        else logging.Formatter(
            "%(levelname)s %(name)s %(message)s %(audit)s", defaults={"audit": {}}
        )
    )
    logging.basicConfig(level=settings.log_level, handlers=[handler])
    logging.getLogger("market").setLevel(settings.log_level)
    # Avoid HTTP client's URL logging: endpoint query strings can carry sensitive data.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)


def event(name, level=logging.INFO, **fields):
    data = {**CONTEXT.get(), **fields}
    clean = {}
    for key, value in data.items():
        if key not in FIELDS:
            continue
        if isinstance(value, str):
            if key in {
                "model",
                "provider_request_id",
                "provider_response_id",
                "call_id",
                "job_id",
                "snapshot_id",
                "extraction_id",
                "batch_id",
                "run_id",
            }:
                value = (
                    value
                    if re.fullmatch(r"[A-Za-z0-9._:/-]{1,160}", value) and "sk-" not in value
                    else "<redacted>"
                )
            value = value[:200]
        clean[key] = value
    LOGGER.log(level, name, extra={"audit": clean})


@contextmanager
def audit_context(**fields):
    token = CONTEXT.set({**CONTEXT.get(), **fields})
    try:
        yield
    finally:
        CONTEXT.reset(token)
