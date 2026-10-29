from __future__ import annotations

import csv
import io
import json
from enum import Enum
from pathlib import Path

from rich.console import Console
from rich.table import Table

from linkedin_jobs_cli.models import JobPosting


class OutputFormat(str, Enum):
    json = "json"
    jsonl = "jsonl"
    csv = "csv"
    table = "table"


def _serializable(jobs: list[JobPosting]) -> list[dict[str, object]]:
    return [job.to_dict() for job in jobs]


def render(jobs: list[JobPosting], output_format: OutputFormat) -> str:
    rows = _serializable(jobs)
    if output_format is OutputFormat.json:
        return json.dumps(rows, ensure_ascii=False, indent=2) + "\n"
    if output_format is OutputFormat.jsonl:
        return "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows)
    if output_format is OutputFormat.csv:
        stream = io.StringIO()
        fieldnames = [
            "id",
            "title",
            "companyName",
            "location",
            "postedAt",
            "employmentType",
            "seniorityLevel",
            "applicantsCount",
            "link",
            "descriptionText",
        ]
        writer = csv.DictWriter(stream, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
        return stream.getvalue()

    stream = io.StringIO()
    console = Console(file=stream, force_terminal=False, color_system=None, width=140)
    table = Table(show_lines=False)
    table.add_column("Posted", width=10)
    table.add_column("Title", max_width=42)
    table.add_column("Company", max_width=24)
    table.add_column("Location", max_width=30)
    table.add_column("URL", max_width=32, overflow="fold")
    for job in jobs:
        table.add_row(job.posted_at or "", job.title, job.company_name, job.location, job.link)
    console.print(table)
    return stream.getvalue()


def emit(jobs: list[JobPosting], output_format: OutputFormat, output: Path | None) -> None:
    content = render(jobs, output_format)
    if output:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(content, encoding="utf-8")
    else:
        print(content, end="")
