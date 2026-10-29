from linkedin_jobs_cli.search import StructuredSearch, source_from_url


def test_structured_search_keeps_current_filters_and_converts_legacy_filters() -> None:
    source = StructuredSearch(
        keywords="software engineer",
        location="London, United Kingdom",
        date_posted="pastWeek",
        company_ids=["1441"],
        under_10_applicants=True,
        easy_apply=True,
        experience_levels=["entry", "associate"],
        job_types=["full-time"],
        workplace_types=["remote", "hybrid"],
        sort_by="date",
    ).to_source()

    assert source.params["f_TPR"] == "r604800"
    assert source.params["f_C"] == "1441"
    assert source.params["f_EA"] == "true"
    assert source.params["f_AL"] == "true"
    assert "f_E" not in source.params
    assert "f_JT" not in source.params
    assert "f_WT" not in source.params
    assert "experience level: Entry level or Associate" in source.params["keywords"]
    assert "job type: Full-time" in source.params["keywords"]
    assert "workplace: Remote or Hybrid" in source.params["keywords"]
    assert "sorted by newest jobs" in source.params["keywords"]


def test_search_url_converts_deprecated_filters_and_drops_tracking() -> None:
    source = source_from_url(
        "https://www.linkedin.com/jobs/search/?keywords=engineer&location=London"
        "&f_TPR=r86400&f_E=2%2C3&f_JT=F&f_WT=2&sortBy=DD&trk=public_jobs"
    )

    assert source.params["f_TPR"] == "r86400"
    assert "f_E" not in source.params
    assert "sortBy" not in source.params
    assert "trk" not in source.params
    assert "Entry level or Associate" in source.params["keywords"]
    assert "Full-time" in source.params["keywords"]
    assert "Remote" in source.params["keywords"]


def test_search_url_can_preserve_legacy_filters() -> None:
    source = source_from_url(
        "https://www.linkedin.com/jobs/search/?keywords=engineer&f_JT=F&sortBy=DD",
        auto_convert=False,
    )

    assert source.params["f_JT"] == "F"
    assert source.params["sortBy"] == "DD"
