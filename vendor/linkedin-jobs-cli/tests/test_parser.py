from pathlib import Path

from linkedin_jobs_cli.parser import enrich_job_from_detail, parse_search_results


FIXTURES = Path(__file__).parent / "fixtures"


def test_parse_public_search_cards() -> None:
    jobs = parse_search_results(
        (FIXTURES / "search.html").read_text(),
        source_url="https://www.linkedin.com/jobs/search/?keywords=engineer",
    )

    assert [job.id for job in jobs] == ["4457552683", "4484053318"]
    assert jobs[0].title == "Java Software Engineer III"
    assert jobs[0].company_name == "Example Corp"
    assert jobs[0].company_linkedin_url == "https://www.linkedin.com/company/example"
    assert jobs[0].posted_at == "2026-10-24"
    assert jobs[0].benefits == ["Actively Hiring"]
    assert jobs[1].location == "Remote"


def test_parse_public_job_details() -> None:
    job = parse_search_results((FIXTURES / "search.html").read_text())[0]
    enrich_job_from_detail(job, (FIXTURES / "detail.html").read_text())

    assert job.location == "Greater London, England, United Kingdom"
    assert job.applicants_count == "190"
    assert job.description_text == "Job Description\nBuild reliable software."
    assert "<strong>Job Description</strong>" in (job.description_html or "")
    assert job.seniority_level == "Associate"
    assert job.employment_type == "Full-time"
    assert job.industries == "Financial Services"
    assert job.apply_method == "Offsite"
    assert job.detail_scraped is True


def test_actor_compatible_output_keys() -> None:
    job = parse_search_results((FIXTURES / "search.html").read_text())[0]
    data = job.to_dict()

    assert data["companyName"] == "Example Corp"
    assert data["companyLinkedinUrl"] == "https://www.linkedin.com/company/example"
    assert data["postedAt"] == "2026-10-24"
    assert data["detailScraped"] is False
