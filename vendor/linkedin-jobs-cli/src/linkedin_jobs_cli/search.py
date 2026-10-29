from __future__ import annotations

from dataclasses import dataclass, field
from urllib.parse import parse_qs, urlsplit

from linkedin_jobs_cli.models import SearchSource


DATE_CODES = {
    "anytime": None,
    "any": None,
    "past24hours": "r86400",
    "day": "r86400",
    "pastweek": "r604800",
    "week": "r604800",
    "pastmonth": "r2592000",
    "month": "r2592000",
}

EXPERIENCE_CODES = {
    "1": "Internship",
    "2": "Entry level",
    "3": "Associate",
    "4": "Mid-Senior level",
    "5": "Director",
    "6": "Executive",
}

JOB_TYPE_CODES = {
    "F": "Full-time",
    "P": "Part-time",
    "C": "Contract",
    "T": "Temporary",
    "V": "Volunteer",
    "I": "Internship",
    "O": "Other",
}

WORKPLACE_CODES = {
    "1": "On-site",
    "2": "Remote",
    "3": "Hybrid",
}

EXPERIENCE_ALIASES = {
    "internship": "1",
    "entry": "2",
    "entry-level": "2",
    "associate": "3",
    "mid-senior": "4",
    "mid-senior-level": "4",
    "director": "5",
    "executive": "6",
}

JOB_TYPE_ALIASES = {
    "full-time": "F",
    "fulltime": "F",
    "part-time": "P",
    "parttime": "P",
    "contract": "C",
    "temporary": "T",
    "volunteer": "V",
    "internship": "I",
    "other": "O",
}

WORKPLACE_ALIASES = {
    "on-site": "1",
    "onsite": "1",
    "remote": "2",
    "hybrid": "3",
}

KEPT_AI_FILTERS = {
    "keywords",
    "location",
    "geoId",
    "distance",
    "f_TPR",
    "f_C",
    "f_AL",
    "f_EA",
}

IGNORED_QUERY_KEYS = {
    "currentJobId",
    "pageNum",
    "position",
    "refId",
    "start",
    "trackingId",
    "trk",
}


class SearchInputError(ValueError):
    pass


def _normalise(value: str) -> str:
    return value.strip().lower().replace("_", "-").replace(" ", "-")


def _map_values(values: list[str], aliases: dict[str, str], name: str) -> list[str]:
    mapped: list[str] = []
    valid_codes = set(aliases.values())
    for value in values:
        code = value if value in valid_codes else aliases.get(_normalise(value))
        if code is None:
            choices = ", ".join(sorted(aliases))
            raise SearchInputError(f"Unsupported {name} {value!r}. Use one of: {choices}")
        if code not in mapped:
            mapped.append(code)
    return mapped


def _split_codes(value: str | None) -> list[str]:
    if not value:
        return []
    return [part.strip() for part in value.split(",") if part.strip()]


def _legacy_phrases(params: dict[str, str]) -> list[str]:
    phrases: list[str] = []

    experience = [EXPERIENCE_CODES[code] for code in _split_codes(params.get("f_E")) if code in EXPERIENCE_CODES]
    if experience:
        phrases.append(f"experience level: {' or '.join(experience)}")

    job_types = [JOB_TYPE_CODES[code] for code in _split_codes(params.get("f_JT")) if code in JOB_TYPE_CODES]
    if job_types:
        phrases.append(f"job type: {' or '.join(job_types)}")

    workplaces = [WORKPLACE_CODES[code] for code in _split_codes(params.get("f_WT")) if code in WORKPLACE_CODES]
    if params.get("f_WRA", "").lower() == "true" and "Remote" not in workplaces:
        workplaces.append("Remote")
    if workplaces:
        phrases.append(f"workplace: {' or '.join(workplaces)}")

    if params.get("sortBy") == "DD":
        phrases.append("sorted by newest jobs")

    return phrases


def _append_phrases(keywords: str, phrases: list[str]) -> str:
    if not phrases:
        return keywords.strip()
    suffix = "; ".join(phrases)
    return f"{keywords.strip()}; {suffix}" if keywords.strip() else suffix


def source_from_url(url: str, *, auto_convert: bool = True) -> SearchSource:
    parsed = urlsplit(url)
    hostname = (parsed.hostname or "").lower()
    if hostname != "linkedin.com" and not hostname.endswith(".linkedin.com"):
        raise SearchInputError("Search URLs must use a linkedin.com hostname")
    if "/jobs/search" not in parsed.path:
        raise SearchInputError("Search URLs must point to LinkedIn's /jobs/search page")

    query = parse_qs(parsed.query, keep_blank_values=False)
    params = {key: values[-1] for key, values in query.items() if values and key not in IGNORED_QUERY_KEYS}
    warnings: list[str] = []

    if "f_TP" in params and "f_TPR" not in params:
        params["f_TPR"] = params.pop("f_TP")

    if auto_convert:
        phrases = _legacy_phrases(params)
        params["keywords"] = _append_phrases(params.get("keywords", ""), phrases)
        removed = sorted(set(params) - KEPT_AI_FILTERS)
        for key in removed:
            params.pop(key, None)
        unsupported = sorted({key for key in removed if key not in {"f_E", "f_JT", "f_WT", "f_WRA", "sortBy"}})
        if unsupported:
            warnings.append(
                "LinkedIn no longer supports these URL filters and they could not be converted: "
                + ", ".join(unsupported)
            )

    return SearchSource(label=url, params=params, original_url=url, warnings=warnings)


@dataclass(slots=True)
class StructuredSearch:
    keywords: str = ""
    location: str = ""
    geo_id: str | None = None
    distance: int | None = None
    date_posted: str = "anyTime"
    company_ids: list[str] = field(default_factory=list)
    under_10_applicants: bool = False
    easy_apply: bool = False
    experience_levels: list[str] = field(default_factory=list)
    job_types: list[str] = field(default_factory=list)
    workplace_types: list[str] = field(default_factory=list)
    sort_by: str = "relevance"
    auto_convert: bool = True

    def to_source(self) -> SearchSource:
        if not self.keywords.strip() and not self.location.strip() and not self.geo_id and not self.company_ids:
            raise SearchInputError("Provide keywords, location, geo ID, company IDs, or a LinkedIn search URL")
        if self.distance is not None and self.distance < 0:
            raise SearchInputError("Distance must be zero or greater")

        date_key = _normalise(self.date_posted).replace("-", "")
        if date_key not in DATE_CODES:
            raise SearchInputError("Date posted must be anyTime, past24Hours, pastWeek, or pastMonth")

        params: dict[str, str] = {}
        keywords = self.keywords.strip()
        if self.location.strip():
            params["location"] = self.location.strip()
        if self.geo_id:
            params["geoId"] = self.geo_id
        if self.distance is not None:
            params["distance"] = str(self.distance)
        if DATE_CODES[date_key]:
            params["f_TPR"] = DATE_CODES[date_key] or ""
        if self.company_ids:
            invalid = [company_id for company_id in self.company_ids if not company_id.isdigit()]
            if invalid:
                raise SearchInputError("Company IDs must be numeric: " + ", ".join(invalid))
            params["f_C"] = ",".join(dict.fromkeys(self.company_ids))
        if self.under_10_applicants:
            params["f_EA"] = "true"
        if self.easy_apply:
            params["f_AL"] = "true"

        experience_codes = _map_values(self.experience_levels, EXPERIENCE_ALIASES, "experience level")
        job_type_codes = _map_values(self.job_types, JOB_TYPE_ALIASES, "job type")
        workplace_codes = _map_values(self.workplace_types, WORKPLACE_ALIASES, "workplace type")

        legacy_params: dict[str, str] = {}
        if experience_codes:
            legacy_params["f_E"] = ",".join(experience_codes)
        if job_type_codes:
            legacy_params["f_JT"] = ",".join(job_type_codes)
        if workplace_codes:
            legacy_params["f_WT"] = ",".join(workplace_codes)
        if _normalise(self.sort_by) in {"date", "newest"}:
            legacy_params["sortBy"] = "DD"
        elif _normalise(self.sort_by) != "relevance":
            raise SearchInputError("Sort order must be relevance or date")

        if self.auto_convert:
            keywords = _append_phrases(keywords, _legacy_phrases(legacy_params))
        else:
            params.update(legacy_params)

        if keywords:
            params["keywords"] = keywords
        return SearchSource(label="structured search", params=params)
