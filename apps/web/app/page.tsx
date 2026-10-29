"use client";
import { useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
type Monitor = {
  id: string;
  version: number;
  name: string;
  keywords: string;
  location: string;
  max_items: number;
  enabled: boolean;
  timezone: string;
  daily_time: string;
  weekly_overlap: boolean;
  search_url: string | null;
  geo_id: string | null;
  distance: number | null;
};
type Meta = {
  source_health: {
    state: string;
    reason: string | null;
    retry_at: string | null;
    auth_mode: string;
  };
  collected_jobs: number;
  collected_parsed: number;
  partial_only_jobs: number;
  latest_collection_at: string | null;
  demo: boolean;
  eligible: number;
  parsed: number;
  data_as_of: string | null;
  small_sample: boolean;
  capped: boolean;
  incomplete_runs: number;
  stale: boolean;
  from: string;
  to: string;
};
type Day = {
  date: string;
  newly_observed: number | null;
  capped: boolean;
  partial_day: boolean;
};
type Overview = {
  jobs: number;
  parsed: number;
  salary_disclosed: number;
  salary_disclosure_rate: number | null;
  remote: number;
  remote_classifiable: number;
  daily: Day[];
};
type Skill = {
  skill: string;
  requiredness: string;
  alternative: boolean;
  count: number;
  denominator: number;
  percentage: number | null;
  examples: { job_id: string; quote: string }[];
};
type Job = {
  description_cached: boolean;
  description_fetched_at: string | null;
  collection_state: string;
  description_available: boolean;
  detail_error: string | null;
  id: string;
  title: string;
  company: string;
  location: string;
  url: string;
  posted_at: string | null;
  first_seen_at: string;
  last_seen_at: string;
};
type Run = {
  id: string;
  date: string;
  kind: string;
  state: string;
  mode: string;
  diagnostics: Record<string, unknown>;
  error: string | null;
};
type Salaries = {
  disclosed: number;
  eligible: number;
  method: string;
  groups: {
    currency: string;
    period: string;
    n: number;
    median: number | null;
    min: number;
    max: number;
  }[];
};
const tabs = [
  "Overview",
  "Skills",
  "Titles",
  "Salaries",
  "Jobs",
  "Insights",
  "Preferences & runs",
];
const API = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";
const date = (s: string | null) =>
  s ? new Date(s).toLocaleString("en-GB") : "Not collected";
const percent = (v: number | null) =>
  v === null ? "Unknown" : `${(100 * v).toFixed(0)}%`;
const blank = {
  name: "Software Engineer",
  keywords: "software engineer",
  location: "United Kingdom",
  max_items: 25,
  enabled: true,
  timezone: "Europe/London",
  daily_time: "06:00",
  weekly_overlap: true,
  search_url: null as string | null,
  geo_id: null as string | null,
  distance: null as number | null,
};
export default function Dashboard() {
  const [monitors, setMonitors] = useState<Monitor[]>([]),
    [mid, setMid] = useState(""),
    [tab, setTab] = useState("Overview");
  const [overview, setOverview] = useState<Overview | null>(null),
    [meta, setMeta] = useState<Meta | null>(null),
    [skills, setSkills] = useState<Skill[]>([]);
  const [titles, setTitles] = useState<Record<string, Record<string, number>>>(
      {},
    ),
    [salaries, setSalaries] = useState<Salaries | null>(null),
    [jobs, setJobs] = useState<Job[]>([]),
    [runs, setRuns] = useState<Run[]>([]);
  const [insights, setInsights] = useState<
      { status: string; text: string; job_ids: string[] }[]
    >([]),
    [detail, setDetail] = useState<{
      description: string;
      extraction: unknown;
    } | null>(null);
  const [error, setError] = useState(""),
    [notice, setNotice] = useState(""),
    [busy, setBusy] = useState(false),
    [loading, setLoading] = useState(true),
    [key, setKey] = useState("");
  const [draft, setDraft] = useState({ ...blank }),
    [editing, setEditing] = useState(false),
    [search, setSearch] = useState(""),
    [cursor, setCursor] = useState<string | null>(null),
    [days, setDays] = useState("28");
  const loadSequence = useRef(0);
  const selected = monitors.find((m) => m.id === mid);
  const request = useCallback(
    async (path: string, options: RequestInit = {}) => {
      const r = await fetch(`${API}/api/v1${path}`, {
        ...options,
        headers: {
          "Content-Type": "application/json",
          ...(key ? { "X-Admin-Key": key } : {}),
          ...options.headers,
        },
      });
      if (!r.ok) {
        const body = await r.json().catch(() => ({}));
        throw new Error(
          typeof body.detail === "string"
            ? body.detail
            : `Request failed (${r.status}). Check inputs and API configuration.`,
        );
      }
      return r.json();
    },
    [key],
  );
  const load = useCallback(async () => {
    const sequence = ++loadSequence.current;
    setLoading(true);
    setError("");
    try {
      const m = await request("/monitors");
      if (sequence !== loadSequence.current) return;
      setMonitors(m.data);
      const id = m.data.some((monitor: Monitor) => monitor.id === mid)
        ? mid
        : m.data[0]?.id;
      if (!id) return;
      if (mid !== id) setMid(id);
      const end = new Date();
      const start = new Date(end);
      start.setDate(start.getDate() - Number(days) + 1);
      const window = `?from=${start.toLocaleDateString("en-CA", { timeZone: "Europe/London" })}&to=${end.toLocaleDateString("en-CA", { timeZone: "Europe/London" })}`;
      const [o, s, t, p, j, r, i] = await Promise.all(
        [
          "overview",
          "skills",
          "titles",
          "salaries",
          "jobs",
          "runs",
          "insights",
        ].map((v) =>
          request(`/monitors/${id}/${v}${v === "runs" ? "" : window}`),
        ),
      );
      if (sequence !== loadSequence.current) return;
      setOverview(o.data);
      setMeta(o.meta);
      setSkills(s.data);
      setTitles(t.data);
      setSalaries(p.data);
      setJobs(j.data);
      setCursor(j.next_cursor);
      setRuns(r.data);
      setInsights(i.data);
    } catch (e) {
      if (sequence !== loadSequence.current) return;
      setError(
        e instanceof Error
          ? e.message
          : "Cannot reach the API. Start the backend and try again.",
      );
    } finally {
      if (sequence === loadSequence.current) setLoading(false);
    }
  }, [request, mid, days]);
  useEffect(() => {
    const timer = setTimeout(() => {
      void load();
    }, 0);
    return () => {
      clearTimeout(timer);
      loadSequence.current += 1;
    };
  }, [load]);
  async function action(fn: () => Promise<void>) {
    setBusy(true);
    setError("");
    setNotice("");
    try {
      await fn();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Action failed");
    } finally {
      setBusy(false);
    }
  }
  async function refresh() {
    await action(async () => {
      const r = await request(`/monitors/${mid}/refresh`, { method: "POST" });
      setNotice(`Run ${r.state}. Start the worker to process it (${r.mode}).`);
      await load();
    });
  }
  async function save(e: React.FormEvent) {
    e.preventDefault();
    await action(async () => {
      const m = await request(editing ? `/monitors/${mid}` : "/monitors", {
        method: editing ? "PATCH" : "POST",
        body: JSON.stringify(draft),
      });
      setMid(m.id);
      setEditing(false);
      setNotice("Monitor saved. Edits start a new query version.");
      await load();
    });
  }
  async function openJob(id: string) {
    await action(async () => {
      setDetail(await request(`/jobs/linkedin/${encodeURIComponent(id)}`));
      setTab("Jobs");
    });
  }
  async function findJobs(append = false) {
    await action(async () => {
      const r = await request(
        `/monitors/${mid}/jobs?from=${meta?.from || ""}&to=${meta?.to || ""}&search=${encodeURIComponent(search)}${append && cursor ? `&cursor=${encodeURIComponent(cursor)}` : ""}`,
      );
      setJobs(append ? [...jobs, ...r.data] : r.data);
      setCursor(r.next_cursor);
    });
  }
  const skillTable = (
    <div className="tablewrap">
      <table>
        <thead>
          <tr>
            <th>Skill</th>
            <th>Interpretation</th>
            <th>Jobs / parsed</th>
            <th>Share</th>
            <th>Evidence</th>
          </tr>
        </thead>
        <tbody>
          {skills.map((s, i) => (
            <tr key={s.skill + s.requiredness + i}>
              <td>
                <strong>{s.skill}</strong>
              </td>
              <td>
                {s.requiredness}
                {s.alternative ? " · alternative" : ""}
              </td>
              <td>
                {s.count} / {s.denominator}
              </td>
              <td>{percent(s.percentage)}</td>
              <td>
                {s.examples.slice(0, 1).map((e) => (
                  <button
                    className="textbutton"
                    key={e.job_id}
                    onClick={() => openJob(e.job_id)}
                  >
                    Read source excerpt
                  </button>
                ))}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      {!skills.length && (
        <p className="empty">
          No verified candidate requirements in this sample.
        </p>
      )}
    </div>
  );
  return (
    <div className="shell">
      <aside>
        <Link className="brand" href="/">
          Fieldwork<span>Job market intelligence</span>
        </Link>
        <nav aria-label="Dashboard">
          {tabs.map((t) => (
            <button
              key={t}
              className={tab === t ? "active" : ""}
              aria-current={tab === t ? "page" : undefined}
              onClick={() => {
                setTab(t);
                setDetail(null);
              }}
            >
              {t}
            </button>
          ))}
        </nav>
        <div className="sidebar-note">
          Your observed market.
          <br />
          Every claim has a source.
          <p>
            LinkedIn public search
            <br />
            via linkedin-jobs-cli
          </p>
        </div>
      </aside>
      <main>
        <header>
          <div>
            <span className="status-dot" />
            Personal workspace
          </div>
          <button
            className="textbutton"
            onClick={() => void load()}
            disabled={loading}
          >
            Reload data
          </button>
        </header>
        {meta?.demo && (
          <div className="demo">
            Demo workspace · Synthetic fixtures. These figures do not describe
            the live job market.
          </div>
        )}
        <div className="pageheading">
          <div>
            <h1>{tab === "Overview" ? "Your market, observed." : tab}</h1>
            <p>
              {selected
                ? `${selected.name} · ${selected.location}`
                : "Create a monitor to begin collecting evidence."}
            </p>
          </div>
          {mid && (
            <button onClick={refresh} disabled={busy || loading}>
              Refresh now
            </button>
          )}
        </div>
        <div className="filters">
          <label>
            Monitor
            <select
              value={mid}
              onChange={(e) => {
                setMid(e.target.value);
                setDetail(null);
              }}
            >
              <option value="" disabled>
                Select a monitor
              </option>
              {monitors.map((m) => (
                <option key={m.id} value={m.id}>
                  {m.name} · v{m.version}
                </option>
              ))}
            </select>
          </label>
          <label>
            Observation window
            <select value={days} onChange={(e) => setDays(e.target.value)}>
              <option value="7">Last 7 days</option>
              <option value="28">Last 28 days</option>
            </select>
          </label>
          <div className="freshness">
            Latest collection
            <br />
            <strong>
              {date(meta?.latest_collection_at || meta?.data_as_of || null)}
            </strong>
          </div>
        </div>
        {error && (
          <div role="alert" className="error">
            {error} <button onClick={() => void load()}>Retry</button>
          </div>
        )}
        {notice && (
          <p role="status" className="notice">
            {notice}
          </p>
        )}
        {loading && (
          <p role="status" className="empty">
            Loading observations…
          </p>
        )}
        {(tab === "Preferences & runs" || (!loading && !monitors.length)) && (
          <>
            <section>
              <h2>{editing ? "Edit monitor" : "Create a monitor"}</h2>
              <p>
                Daily collection uses a recent-posting window. Saving edits
                creates an immutable query version.
              </p>
              <form onSubmit={save} className="monitorform">
                <label>
                  Monitor name
                  <input
                    required
                    value={draft.name}
                    onChange={(e) =>
                      setDraft({ ...draft, name: e.target.value })
                    }
                  />
                </label>
                <label>
                  Keywords
                  <input
                    required
                    value={draft.keywords}
                    onChange={(e) =>
                      setDraft({ ...draft, keywords: e.target.value })
                    }
                  />
                </label>
                <label>
                  Location
                  <input
                    value={draft.location}
                    onChange={(e) =>
                      setDraft({ ...draft, location: e.target.value })
                    }
                  />
                </label>
                <label>
                  Maximum jobs per run
                  <input
                    type="number"
                    min="1"
                    max="100"
                    required
                    value={draft.max_items}
                    onChange={(e) =>
                      setDraft({ ...draft, max_items: Number(e.target.value) })
                    }
                  />
                </label>
                <label>
                  Daily time ({draft.timezone})
                  <input
                    type="time"
                    required
                    value={draft.daily_time}
                    onChange={(e) =>
                      setDraft({ ...draft, daily_time: e.target.value })
                    }
                  />
                </label>
                <label>
                  Search URL (optional, replaces keyword/location filters)
                  <input
                    type="url"
                    value={draft.search_url || ""}
                    onChange={(e) =>
                      setDraft({ ...draft, search_url: e.target.value || null })
                    }
                  />
                </label>
                <label className="check">
                  <input
                    type="checkbox"
                    checked={draft.enabled}
                    onChange={(e) =>
                      setDraft({ ...draft, enabled: e.target.checked })
                    }
                  />
                  Collection enabled
                </label>
                <label className="check">
                  <input
                    type="checkbox"
                    checked={draft.weekly_overlap}
                    onChange={(e) =>
                      setDraft({ ...draft, weekly_overlap: e.target.checked })
                    }
                  />
                  Sunday overlap
                </label>
                <div className="formactions">
                  <button disabled={busy}>Save monitor</button>
                  {selected && !editing && (
                    <button
                      type="button"
                      className="secondary"
                      onClick={() => {
                        const config = { ...blank };
                        for (const k of Object.keys(
                          config,
                        ) as (keyof typeof blank)[])
                          Object.assign(config, { [k]: selected[k] });
                        setDraft(config);
                        setEditing(true);
                      }}
                    >
                      Edit selected monitor
                    </button>
                  )}
                </div>
              </form>
            </section>
            <section>
              <h2>Access</h2>
              <p>
                If the API requires an admin key, enter it here. It stays in
                memory for this tab.
              </p>
              <label>
                Admin key
                <input
                  type="password"
                  autoComplete="off"
                  value={key}
                  onChange={(e) => setKey(e.target.value)}
                />
              </label>
            </section>
          </>
        )}
        {!loading && mid && meta && (
          <>
            {(meta.stale || meta.incomplete_runs > 0 || meta.capped) && (
              <div className="quality">
                {meta.stale ? "No recent complete collection. " : ""}
                {meta.incomplete_runs > 0
                  ? `${meta.incomplete_runs} run(s) have incomplete collection or description coverage. `
                  : ""}
                {meta.capped
                  ? "A result cap was reached; this sample may be truncated."
                  : ""}
              </div>
            )}
            {meta.source_health?.state !== "available" &&
              meta.source_health && (
                <div className="quality" role="status">
                  Source access unavailable: {meta.source_health.reason}. Guest
                  collection is paused.
                  {meta.source_health.retry_at
                    ? ` Next eligible attempt: ${date(meta.source_health.retry_at)}.`
                    : " Review source access before explicitly resuming the worker."}{" "}
                  Saved jobs remain available. No account credentials are
                  stored.
                </div>
              )}
            {meta.partial_only_jobs > 0 && (
              <div className="quality" role="status">
                {meta.collected_jobs} jobs collected; {meta.collected_parsed}{" "}
                descriptions parsed. {meta.partial_only_jobs} jobs are available
                only from partial collections and are excluded from trends.{" "}
                <button className="textbutton" onClick={() => setTab("Jobs")}>
                  Inspect collected jobs
                </button>
              </div>
            )}
            {tab === "Overview" && overview && (
              <>
                <div className="summary">
                  <p>
                    <strong>{overview.jobs}</strong> unique jobs eligible for
                    trends
                  </p>
                  <p>
                    <strong>
                      {overview.parsed} / {overview.jobs}
                    </strong>{" "}
                    descriptions parsed
                  </p>
                  <p>
                    <strong>{percent(overview.salary_disclosure_rate)}</strong>{" "}
                    advertised pay disclosure
                  </p>
                  <p>
                    <strong>
                      {overview.remote} / {overview.remote_classifiable}
                    </strong>{" "}
                    classified jobs are remote
                  </p>
                </div>
                <section>
                  <div className="sectionheading">
                    <div>
                      <h2>Newly observed postings</h2>
                      <p>
                        Unique jobs first seen in this query version ·{" "}
                        {meta.from} to {meta.to} · {selected?.timezone}
                      </p>
                    </div>
                    <span className="tag">
                      {meta.small_sample
                        ? "Small sample · N < 30"
                        : `N = ${meta.eligible}`}
                    </span>
                  </div>
                  <div
                    className="chart"
                    role="img"
                    aria-label="Daily newly observed jobs. Missing collection days have gaps."
                  >
                    {overview.daily.map((d, i) => (
                      <div
                        className="day"
                        key={d.date}
                        title={`${d.date}: ${d.newly_observed === null ? "not collected" : `${d.newly_observed} jobs`}${d.partial_day ? " · partial day" : ""}`}
                      >
                        <span
                          className={d.newly_observed === null ? "gap" : "bar"}
                          style={{
                            height:
                              d.newly_observed === null
                                ? "2px"
                                : `${Math.max(3, (d.newly_observed / Math.max(1, ...overview.daily.map((x) => x.newly_observed || 0))) * 140)}px`,
                          }}
                        />
                        {(i % 7 === 0 || i === overview.daily.length - 1) && (
                          <small>{d.date.slice(5)}</small>
                        )}
                      </div>
                    ))}
                  </div>
                  <p className="chartnote">
                    Gaps mean no healthy daily collection. Zero means a
                    completed run found no newly observed jobs. Today is
                    partial.
                  </p>
                  <details>
                    <summary>Read daily counts</summary>
                    <table>
                      <thead>
                        <tr>
                          <th>Date</th>
                          <th>Newly observed jobs</th>
                        </tr>
                      </thead>
                      <tbody>
                        {overview.daily.map((d) => (
                          <tr key={d.date}>
                            <td>{d.date}</td>
                            <td>
                              {d.newly_observed ?? "Not collected"}
                              {d.partial_day ? " · partial day" : ""}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </details>
                </section>
                <section>
                  <h2>Requirements in the sample</h2>
                  <p>
                    Alternatives are labelled and excluded from independent
                    strict must-haves.
                  </p>
                  {skillTable}
                </section>
              </>
            )}
            {tab === "Skills" && (
              <section>
                <h2>Skills with supporting evidence</h2>
                <p>
                  Denominator: {meta.parsed} parsed descriptions. Coverage:{" "}
                  {meta.parsed} / {meta.eligible} observed jobs.
                </p>
                {skillTable}
              </section>
            )}
            {tab === "Titles" &&
              Object.entries(titles).map(([group, values]) => (
                <section key={group}>
                  <h2>{group.replaceAll("_", " ")}</h2>
                  <table>
                    <thead>
                      <tr>
                        <th>Classification</th>
                        <th>Unique jobs</th>
                      </tr>
                    </thead>
                    <tbody>
                      {Object.entries(values).map(([name, n]) => (
                        <tr key={name}>
                          <td>{name}</td>
                          <td>{n}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                  {!Object.keys(values).length && (
                    <p>No classifications available.</p>
                  )}
                </section>
              ))}
            {tab === "Salaries" && salaries && (
              <section>
                <h2>Explicitly advertised compensation</h2>
                <p>
                  {salaries.disclosed} / {salaries.eligible} jobs disclose pay.{" "}
                  {salaries.method}
                </p>
                {!salaries.groups.length ? (
                  <div className="empty">
                    Not enough comparable salary data. Undisclosed pay stays
                    unknown.
                  </div>
                ) : (
                  <table>
                    <thead>
                      <tr>
                        <th>Currency / period</th>
                        <th>N</th>
                        <th>Median</th>
                        <th>Observed midpoint range</th>
                      </tr>
                    </thead>
                    <tbody>
                      {salaries.groups.map((g) => (
                        <tr key={g.currency + g.period}>
                          <td>
                            {g.currency} / {g.period}
                          </td>
                          <td>{g.n}</td>
                          <td>
                            {g.median?.toLocaleString("en-GB") ??
                              "Not enough data"}
                          </td>
                          <td>
                            {g.min.toLocaleString("en-GB")} –{" "}
                            {g.max.toLocaleString("en-GB")}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                )}
              </section>
            )}
            {tab === "Jobs" && (
              <section>
                <h2>Jobs explorer</h2>
                <p>
                  Saved listings include partial collections. Missing
                  descriptions and incomplete collection states are labelled
                  below.
                </p>
                <form
                  className="search"
                  onSubmit={(e) => {
                    e.preventDefault();
                    void findJobs();
                  }}
                >
                  <label>
                    Search title, employer or location
                    <input
                      value={search}
                      onChange={(e) => setSearch(e.target.value)}
                    />
                  </label>
                  <button disabled={busy}>Search</button>
                </form>
                <div className="tablewrap">
                  <table>
                    <thead>
                      <tr>
                        <th>Job and employer</th>
                        <th>Location</th>
                        <th>Source posting date</th>
                        <th>Evidence</th>
                      </tr>
                    </thead>
                    <tbody>
                      {jobs.map((j) => (
                        <tr key={j.id}>
                          <td>
                            <a
                              href={j.url}
                              target="_blank"
                              rel="noopener noreferrer"
                            >
                              {j.title}
                            </a>
                            <small>{j.company}</small>
                            {j.description_cached && (
                              <small>
                                Cached description · retrieved{" "}
                                {date(j.description_fetched_at)}
                              </small>
                            )}
                            {j.collection_state === "partial" && (
                              <span className="tag">Partial collection</span>
                            )}
                          </td>
                          <td>{j.location}</td>
                          <td>{j.posted_at || "Unknown"}</td>
                          <td>
                            <button
                              className="textbutton"
                              onClick={() => openJob(j.id)}
                            >
                              {j.description_available
                                ? "View description"
                                : "Description unavailable"}
                            </button>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
                {!jobs.length && (
                  <p className="empty">No jobs match this search.</p>
                )}
                {cursor && (
                  <button onClick={() => findJobs(true)} disabled={busy}>
                    Load more
                  </button>
                )}
                {detail && (
                  <article className="evidence">
                    <h3>Source description</h3>
                    <p className="description">
                      {detail.description ||
                        "LinkedIn did not provide a description for this listing. Its listing details were saved, but its requirements could not be extracted."}
                    </p>
                    <details open>
                      <summary>Validated extraction and evidence</summary>
                      <pre>{JSON.stringify(detail.extraction, null, 2)}</pre>
                    </details>
                  </article>
                )}
              </section>
            )}
            {tab === "Insights" && (
              <section>
                <h2>Observations and caveats</h2>
                {insights.map((i, n) => (
                  <article key={n}>
                    <span className="tag">{i.status.replaceAll("_", " ")}</span>
                    <p>{i.text}</p>
                    {i.job_ids.map((id) => (
                      <button
                        className="textbutton"
                        key={id}
                        onClick={() => openJob(id)}
                      >
                        Job {id}
                      </button>
                    ))}
                  </article>
                ))}
                <p>
                  Period comparisons remain insufficient until comparable
                  complete windows are available.
                </p>
              </section>
            )}
            {tab === "Preferences & runs" && (
              <section>
                <h2>Collection runs</h2>
                <p>
                  One daily run per monitor and local calendar day. Repeated
                  refresh clicks return the existing run.
                </p>
                <div className="tablewrap">
                  <table>
                    <thead>
                      <tr>
                        <th>Logical day</th>
                        <th>Kind / mode</th>
                        <th>State</th>
                        <th>Diagnostics</th>
                      </tr>
                    </thead>
                    <tbody>
                      {runs.map((r) => (
                        <tr key={r.id}>
                          <td>{r.date}</td>
                          <td>
                            {r.kind} / {r.mode}
                          </td>
                          <td>
                            <span
                              className={`tag ${r.state === "completed" ? "good" : ""}`}
                            >
                              {r.state}
                            </span>
                            {r.error && <small>{r.error}</small>}
                          </td>
                          <td>
                            <details>
                              <summary>View counts</summary>
                              <pre>
                                {JSON.stringify(r.diagnostics, null, 2)}
                              </pre>
                            </details>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
                {!runs.length && (
                  <p>No runs yet. Use Refresh now and start the worker.</p>
                )}
              </section>
            )}
          </>
        )}
        <footer>
          Collected observations, never a census. Unknown values stay unknown.{" "}
          <a href={`${API}/docs`} target="_blank" rel="noopener noreferrer">
            API documentation
          </a>
        </footer>
      </main>
    </div>
  );
}
