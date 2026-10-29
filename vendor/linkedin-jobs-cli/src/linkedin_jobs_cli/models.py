from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any


@dataclass(slots=True)
class JobPosting:
    id: str
    link: str
    title: str
    company_name: str
    location: str
    company_linkedin_url: str | None = None
    company_logo: str | None = None
    salary_info: list[str] = field(default_factory=list)
    posted_at: str | None = None
    posted_time_ago: str | None = None
    benefits: list[str] = field(default_factory=list)
    description_html: str | None = None
    description_text: str | None = None
    applicants_count: str | None = None
    apply_url: str | None = None
    apply_method: str | None = None
    job_poster_name: str | None = None
    job_poster_title: str | None = None
    job_poster_photo: str | None = None
    job_poster_profile_url: str | None = None
    seniority_level: str | None = None
    employment_type: str | None = None
    job_function: str | None = None
    industries: str | None = None
    company_description: str | None = None
    company_website: str | None = None
    company_employees_count: int | None = None
    salary_insights: Any = None
    expire_at: str | None = None
    work_remote_allowed: bool | None = None
    workplace_types: list[str] = field(default_factory=list)
    posted_at_timestamp: int | None = None
    standardized_title: str | None = None
    country: str | None = None
    insights: list[str] = field(default_factory=list)
    source_url: str | None = None
    detail_scraped: bool = False
    detail_error: str | None = None
    scraped_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())

    def to_dict(self) -> dict[str, Any]:
        values = asdict(self)
        key_map = {
            "company_name": "companyName",
            "company_linkedin_url": "companyLinkedinUrl",
            "company_logo": "companyLogo",
            "salary_info": "salaryInfo",
            "posted_at": "postedAt",
            "posted_time_ago": "postedTimeAgo",
            "description_html": "descriptionHtml",
            "description_text": "descriptionText",
            "applicants_count": "applicantsCount",
            "apply_url": "applyUrl",
            "apply_method": "applyMethod",
            "job_poster_name": "jobPosterName",
            "job_poster_title": "jobPosterTitle",
            "job_poster_photo": "jobPosterPhoto",
            "job_poster_profile_url": "jobPosterProfileUrl",
            "seniority_level": "seniorityLevel",
            "employment_type": "employmentType",
            "job_function": "jobFunction",
            "company_description": "companyDescription",
            "company_website": "companyWebsite",
            "company_employees_count": "companyEmployeesCount",
            "salary_insights": "salaryInsights",
            "expire_at": "expireAt",
            "work_remote_allowed": "workRemoteAllowed",
            "workplace_types": "workplaceTypes",
            "posted_at_timestamp": "postedAtTimestamp",
            "standardized_title": "standardizedTitle",
            "source_url": "sourceUrl",
            "detail_scraped": "detailScraped",
            "detail_error": "detailError",
            "scraped_at": "scrapedAt",
        }
        return {key_map.get(key, key): value for key, value in values.items()}


@dataclass(slots=True)
class SearchSource:
    label: str
    params: dict[str, str]
    original_url: str | None = None
    warnings: list[str] = field(default_factory=list)
