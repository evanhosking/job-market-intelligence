# Data definitions

Job identity is `(source, source_job_id)` and source_job_id is always a nonempty string. Numeric-looking IDs remain strings, including values beyond JavaScript integer precision. A query version records immutable monitor config and the effective run saves the exact source parameters. Version changes create new cohorts.

UTC timestamps are serialized ISO instants; source `postedAt` stays a date-only string when the source only supplies a date. First seen, source posting date, observation time and last seen are independent. A date in the future is not installed as a normalized posting date, but remains in the raw payload. Absence from a later search never means closed or removed.

| Metric | Population and denominator |
| --- | --- |
| Unique observed jobs | Distinct IDs from completed runs, current query version, selected local date range and collection mode |
| Newly observed jobs/day | First successful observation for the query version and mode, grouped by configured timezone; weekly discoveries are first seen, never claimed newly published |
| Skill prevalence | Distinct eligible jobs with matching quoted candidate requirements / successfully extracted eligible descriptions; requiredness and alternatives shown separately |
| Strict requirement | Non-negated candidate requirement with confidence >= .7 and no alternative group |
| Salary disclosure | Jobs with explicit pay disclosure / all eligible observed jobs |
| Comparable pay median | Unambiguous full-time base-pay midpoints within one original currency and pay period; N >= 3; open bounds and conflicting same-period disclosures excluded |
| Work arrangement | Explicit description classification; unknown stays unspecified. Remote denominator excludes unclassifiable values |
| Titles | Raw snapshot titles and normalized seniority; unknown shown separately |

A gap in daily counts means no completed daily run for that date. A successful empty collection is a real zero. Failed, queued and interrupted runs are excluded. For new runs, a partial enrichment phase with an explicitly completed listing phase is eligible for listing-based metrics. Partial/incomplete listing phases and legacy partial runs without stage metadata remain excluded. Cap-reached successful runs remain sampled observations and expose a truncation caveat. A partial current day is labelled; comparisons are currently insufficient_data. No statistical significance or market-wide coverage is implied. Highlight sample threshold is 30.

Snapshot extraction results carry prompt/model/taxonomy versions. Canonical aliases preserve C# versus .NET and map K8s to Kubernetes. Evidence quotes must occur verbatim in cleaned descriptions (or source salary text for rules). Alternative cloud/messaging tools share group identifiers and never become several independent strict must-haves. Company credit/revenue/funding and applicant counts cannot create salary.

`metric_snapshots` keys are query version + selected window/mode + computation method. The reproducible computation is `market.analytics.compute` (method `listing-coverage-v2`); evidence job IDs come from the cohort's observations. Stored source observations and snapshots are the primary provenance. Retain them as long as dependent metrics are published.
