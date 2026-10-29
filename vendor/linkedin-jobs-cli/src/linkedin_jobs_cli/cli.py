from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console

from linkedin_jobs_cli import __version__
from linkedin_jobs_cli.client import LinkedInClientError, LinkedInPublicClient
from linkedin_jobs_cli.output import OutputFormat, emit
from linkedin_jobs_cli.search import SearchInputError, StructuredSearch, source_from_url


app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    help="Search LinkedIn's public guest jobs surface without an account or cookies.",
)
error_console = Console(stderr=True)


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(__version__)
        raise typer.Exit()


@app.callback()
def main(
    version: Annotated[
        bool | None,
        typer.Option("--version", callback=_version_callback, is_eager=True, help="Show the version."),
    ] = None,
) -> None:
    """Public LinkedIn jobs search."""


@app.command()
def search(
    keywords: Annotated[
        str | None,
        typer.Argument(help="Job title, skills, or natural-language search criteria."),
    ] = None,
    url: Annotated[
        list[str] | None,
        typer.Option("--url", help="LinkedIn jobs search URL. Repeat for multiple searches."),
    ] = None,
    location: Annotated[str | None, typer.Option("--location", "-l")] = None,
    geo_id: Annotated[str | None, typer.Option("--geo-id")] = None,
    distance: Annotated[int | None, typer.Option("--distance", min=0, help="Search radius in miles.")] = None,
    date_posted: Annotated[
        str,
        typer.Option("--date-posted", help="anyTime, past24Hours, pastWeek, or pastMonth."),
    ] = "anyTime",
    company_id: Annotated[
        list[str] | None,
        typer.Option("--company-id", help="Numeric LinkedIn company ID. Repeat for multiple companies."),
    ] = None,
    under_10_applicants: Annotated[
        bool,
        typer.Option("--under-10-applicants", help="Request jobs with fewer than ten applicants."),
    ] = False,
    easy_apply: Annotated[
        bool,
        typer.Option("--easy-apply", help="Request Easy Apply jobs."),
    ] = False,
    experience_level: Annotated[
        list[str] | None,
        typer.Option("--experience-level", help="Legacy filter converted into natural-language keywords."),
    ] = None,
    job_type: Annotated[
        list[str] | None,
        typer.Option("--job-type", help="Legacy filter converted into natural-language keywords."),
    ] = None,
    workplace_type: Annotated[
        list[str] | None,
        typer.Option("--workplace-type", help="Legacy filter converted into natural-language keywords."),
    ] = None,
    sort_by: Annotated[
        str,
        typer.Option("--sort-by", help="relevance or date; date is converted into search text."),
    ] = "relevance",
    auto_convert: Annotated[
        bool,
        typer.Option("--auto-convert/--no-auto-convert", help="Convert discontinued URL filters to search text."),
    ] = True,
    limit_per_source: Annotated[
        int,
        typer.Option("--limit-per-source", "--limit", min=1, max=1000),
    ] = 25,
    global_limit: Annotated[
        int | None,
        typer.Option("--global-limit", min=1, help="Optional cap across all search URLs."),
    ] = None,
    details: Annotated[
        bool,
        typer.Option("--details/--no-details", help="Fetch each public job detail page."),
    ] = True,
    output_format: Annotated[
        OutputFormat,
        typer.Option("--format", "-f", case_sensitive=False),
    ] = OutputFormat.json,
    output: Annotated[Path | None, typer.Option("--output", "-o")] = None,
    delay: Annotated[
        float,
        typer.Option("--delay", min=0, help="Minimum delay between LinkedIn requests in seconds."),
    ] = 1,
    timeout: Annotated[float, typer.Option("--timeout", min=1)] = 20,
    verbose: Annotated[bool, typer.Option("--verbose", "-v")] = False,
) -> None:
    """Search jobs and emit JSON, JSONL, CSV, or a terminal table."""
    try:
        if url:
            sources = [source_from_url(search_url, auto_convert=auto_convert) for search_url in url]
            if verbose and any([keywords, location, geo_id, company_id]):
                error_console.print("[yellow]Search URLs supplied; structured search fields are ignored.[/yellow]")
        else:
            structured = StructuredSearch(
                keywords=keywords or "",
                location=location or "",
                geo_id=geo_id,
                distance=distance,
                date_posted=date_posted,
                company_ids=company_id or [],
                under_10_applicants=under_10_applicants,
                easy_apply=easy_apply,
                experience_levels=experience_level or [],
                job_types=job_type or [],
                workplace_types=workplace_type or [],
                sort_by=sort_by,
                auto_convert=auto_convert,
            )
            sources = [structured.to_source()]

        if verbose:
            for source in sources:
                for warning in source.warnings:
                    error_console.print(f"[yellow]{warning}[/yellow]")
            progress = lambda message: error_console.print(f"[dim]{message}[/dim]")
        else:
            progress = None

        with LinkedInPublicClient(timeout=timeout, delay=delay, progress=progress) as client:
            jobs = client.search(
                sources,
                limit_per_source=limit_per_source,
                global_limit=global_limit,
                include_details=details,
            )
        emit(jobs, output_format, output)
    except (LinkedInClientError, SearchInputError, ValueError) as error:
        error_console.print(f"[red]Error:[/red] {error}")
        raise typer.Exit(code=1) from error
