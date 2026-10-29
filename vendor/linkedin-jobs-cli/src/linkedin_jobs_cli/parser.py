from __future__ import annotations

import re
from urllib.parse import urlsplit, urlunsplit

from bs4 import BeautifulSoup, Tag

from linkedin_jobs_cli.models import JobPosting


JOB_ID_PATTERN = re.compile(r"(?:jobPosting:|/jobs/view/(?:[^/?]+-)?)(\d+)")


def _text(element: Tag | None, separator: str = " ") -> str:
    if element is None:
        return ""
    return " ".join(element.get_text(separator, strip=True).split())


def _clean_url(url: str | None) -> str | None:
    if not url:
        return None
    parsed = urlsplit(url)
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path.rstrip("/"), "", ""))


def _job_id(card: Tag) -> str | None:
    urn = card.get("data-entity-urn", "")
    link = card.select_one("a.base-card__full-link")
    candidate = f"{urn} {link.get('href', '') if link else ''}"
    match = JOB_ID_PATTERN.search(candidate)
    return match.group(1) if match else None


def _canonical_job_url(job_id: str) -> str:
    return f"https://www.linkedin.com/jobs/view/{job_id}"


def parse_search_results(html: str, *, source_url: str | None = None) -> list[JobPosting]:
    soup = BeautifulSoup(html, "html.parser")
    jobs: list[JobPosting] = []
    seen: set[str] = set()

    for card in soup.select(".job-search-card"):
        job_id = _job_id(card)
        title = _text(card.select_one(".base-search-card__title"))
        company = _text(card.select_one(".base-search-card__subtitle"))
        if not job_id or not title or not company or job_id in seen:
            continue

        seen.add(job_id)
        company_link = card.select_one(".base-search-card__subtitle a[href]")
        logo = card.select_one("img[data-delayed-url]")
        time = card.select_one("time")
        benefit = _text(card.select_one(".job-posting-benefits__text"))
        salary_parts = [_text(element) for element in card.select(".job-search-card__salary-info span")]
        if not salary_parts:
            salary_text = _text(card.select_one(".job-search-card__salary-info"))
            salary_parts = [salary_text] if salary_text else []

        jobs.append(
            JobPosting(
                id=job_id,
                link=_canonical_job_url(job_id),
                title=title,
                company_name=company,
                company_linkedin_url=_clean_url(company_link.get("href") if company_link else None),
                company_logo=logo.get("data-delayed-url") if logo else None,
                location=_text(card.select_one(".job-search-card__location")),
                salary_info=salary_parts,
                posted_at=time.get("datetime") if time else None,
                posted_time_ago=_text(time),
                benefits=[benefit] if benefit else [],
                source_url=source_url,
            )
        )

    return jobs


def _parse_applicants(caption: str) -> str | None:
    match = re.search(r"([\d,]+)", caption)
    if not match:
        return None
    value = match.group(1).replace(",", "")
    return f"{value}+" if caption.lower().startswith("over ") else value


def _external_apply_url(soup: BeautifulSoup) -> str | None:
    for anchor in soup.select('a[href][data-tracking-control-name*="apply-link-offsite"]'):
        href = anchor.get("href")
        hostname = (urlsplit(href).hostname or "").lower() if href else ""
        if href and hostname and not hostname.endswith("linkedin.com"):
            return href
    return None


def enrich_job_from_detail(job: JobPosting, html: str) -> JobPosting:
    soup = BeautifulSoup(html, "html.parser")

    title = _text(soup.select_one(".top-card-layout__title"))
    company_link = soup.select_one(".topcard__org-name-link[href]")
    company = _text(company_link)
    location = _text(soup.select_one(".topcard__flavor--bullet"))
    logo = soup.select_one(".top-card-layout img[data-delayed-url]")
    posted_time = _text(soup.select_one(".posted-time-ago__text"))
    applicants_caption = _text(soup.select_one(".num-applicants__caption"))
    description = soup.select_one(".show-more-less-html__markup")

    if title:
        job.title = title
    if company:
        job.company_name = company
    if company_link:
        job.company_linkedin_url = _clean_url(company_link.get("href"))
    if location:
        job.location = location
    if logo:
        job.company_logo = logo.get("data-delayed-url")
    if posted_time:
        job.posted_time_ago = posted_time
    if applicants_caption:
        job.applicants_count = _parse_applicants(applicants_caption)
    if description:
        job.description_html = description.decode_contents().strip()
        job.description_text = "\n".join(
            line.strip() for line in description.get_text("\n", strip=True).splitlines() if line.strip()
        )

    criteria: dict[str, str] = {}
    for item in soup.select(".description__job-criteria-item"):
        name = _text(item.select_one(".description__job-criteria-subheader")).lower()
        value = _text(item.select_one(".description__job-criteria-text"))
        if name and value:
            criteria[name] = value
    job.seniority_level = criteria.get("seniority level", job.seniority_level)
    job.employment_type = criteria.get("employment type", job.employment_type)
    job.job_function = criteria.get("job function", job.job_function)
    job.industries = criteria.get("industries", job.industries)

    salary = _text(soup.select_one(".compensation__salary"))
    if salary and salary not in job.salary_info:
        job.salary_info.append(salary)

    recruiter = soup.select_one(".message-the-recruiter")
    if recruiter:
        profile = recruiter.select_one('a[href*="/in/"]')
        photo = recruiter.select_one("img[data-delayed-url]")
        name = _text(recruiter.select_one(".base-main-card__title"))
        subtitle = _text(recruiter.select_one(".base-main-card__subtitle"))
        job.job_poster_name = name or job.job_poster_name
        job.job_poster_title = subtitle or job.job_poster_title
        job.job_poster_profile_url = _clean_url(profile.get("href") if profile else None)
        job.job_poster_photo = photo.get("data-delayed-url") if photo else job.job_poster_photo

    apply_url = _external_apply_url(soup)
    if apply_url:
        job.apply_url = apply_url
        job.apply_method = "Offsite"
    elif soup.select_one('[data-impression-id*="apply-link-offsite"]'):
        job.apply_method = "Offsite"
    elif any("easy apply" in _text(button).lower() for button in soup.select("button")):
        job.apply_method = "Easy Apply"

    insights = [_text(element) for element in soup.select(".job-posting-benefits__text")]
    for insight in insights:
        if insight and insight not in job.benefits:
            job.benefits.append(insight)

    job.detail_scraped = True
    job.detail_error = None
    return job
