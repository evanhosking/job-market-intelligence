from datetime import time
from typing import Literal
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from linkedin_jobs_cli.search import StructuredSearch, source_from_url
from pydantic import BaseModel, ConfigDict, Field, model_validator


class MonitorConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=100)
    keywords: str = Field(default="software engineer", max_length=500)
    location: str = Field(default="United Kingdom", max_length=200)
    geo_id: str | None = Field(default=None, pattern=r"^\d{1,20}$")
    distance: int | None = Field(default=None, ge=0, le=500)
    search_url: str | None = Field(default=None, max_length=3000)
    max_items: int = Field(default=25, ge=1, le=100)
    enabled: bool = True
    timezone: str = "Europe/London"
    daily_time: str = Field(default="06:00", pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d$")
    weekly_overlap: bool = True

    @model_validator(mode="after")
    def validate_source(self):
        try:
            ZoneInfo(self.timezone)
            time.fromisoformat(self.daily_time)
            if self.search_url:
                url = urlsplit(self.search_url)
                if (
                    url.scheme != "https"
                    or url.hostname not in {"linkedin.com", "www.linkedin.com"}
                    or url.port
                    or url.username
                    or url.password
                    or url.path.rstrip("/") != "/jobs/search"
                ):
                    raise ValueError("Use an HTTPS linkedin.com/jobs/search URL")
                allowed = {
                    "keywords",
                    "location",
                    "geoId",
                    "distance",
                    "f_TPR",
                    "f_C",
                    "f_AL",
                    "f_EA",
                    "f_E",
                    "f_JT",
                    "f_WT",
                    "sortBy",
                }
                from urllib.parse import parse_qs

                if set(parse_qs(url.query)) - allowed:
                    raise ValueError("Remove tracking or unsupported query parameters")
                source_from_url(self.search_url)
            elif not self.keywords.strip():
                raise ValueError("Keywords are required in structured mode")
        except Exception as exc:
            raise ValueError(str(exc)) from exc
        return self

    def source(self, kind="daily"):
        if self.search_url:
            source = source_from_url(self.search_url)
        else:
            source = StructuredSearch(
                keywords=self.keywords,
                location=self.location,
                geo_id=self.geo_id,
                distance=self.distance,
            ).to_source()
        # Date window is always controlled by the worker, including URL mode.
        source.params["f_TPR"] = "r604800" if kind == "weekly" else "r86400"
        return source


class Requirement(BaseModel):
    model_config = ConfigDict(extra="forbid")
    canonical_candidate: str = Field(min_length=1)
    surface_text: str
    category: Literal[
        "language",
        "framework",
        "cloud",
        "database",
        "messaging",
        "platform",
        "tool",
        "practice",
        "soft_skill",
        "education",
        "experience",
        "other",
    ]
    requiredness: Literal["required", "preferred", "familiarity", "mentioned", "unclear"]
    context: Literal[
        "candidate_requirement",
        "current_stack",
        "responsibility",
        "benefit",
        "company_description",
        "other",
    ]
    alternatives_group: str | None = None
    explicit_negation: bool = False
    evidence_quote: str = Field(min_length=1)
    evidence_start: int | None = Field(default=None, ge=0)
    evidence_end: int | None = Field(default=None, ge=0)
    confidence: float = Field(ge=0, le=1)


class Pay(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["base_salary", "total_comp", "bonus", "equity", "hourly_rate", "other"] = (
        "base_salary"
    )
    min_amount: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    max_amount: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    currency_iso: Literal["GBP", "USD", "EUR"] | None = None
    period: Literal["hour", "day", "week", "month", "year", "unknown"] = "unknown"
    employment_basis: Literal["full_time", "part_time", "unknown"] = "unknown"
    explicit_in_job_ad: bool = True
    evidence_quote: str = Field(min_length=1)
    confidence: float = Field(ge=0, le=1)

    @model_validator(mode="after")
    def ordered(self):
        if (
            self.min_amount is not None
            and self.max_amount is not None
            and self.min_amount > self.max_amount
        ):
            raise ValueError("Reversed compensation range")
        return self


class JobExtraction(BaseModel):
    model_config = ConfigDict(extra="forbid")
    normalized_title: str | None = None
    role_family: Literal["software_engineering", "other", "unknown"] = "unknown"
    seniority: Literal[
        "intern",
        "junior",
        "mid",
        "senior",
        "staff",
        "principal",
        "lead",
        "manager",
        "director",
        "unknown",
    ] = "unknown"
    seniority_basis: Literal[
        "explicit_title", "explicit_description", "source_field", "inferred", "unknown"
    ] = "unknown"
    work_arrangement: Literal["remote", "hybrid", "onsite", "unspecified"] = "unspecified"
    remote_geo_restriction: str | None = None
    contract_type: Literal[
        "permanent", "contract", "temporary", "internship", "other", "unknown"
    ] = "unknown"
    minimum_experience_years: float | None = Field(default=None, ge=0, le=60)
    degree_requirement: Literal["required", "preferred", "not_stated"] = "not_stated"
    requirements: list[Requirement] = Field(default_factory=list, max_length=100)
    compensation: list[Pay] = Field(default_factory=list, max_length=20)
    extraction_warnings: list[str] = Field(default_factory=list)
