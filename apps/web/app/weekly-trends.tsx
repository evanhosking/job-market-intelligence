"use client";

import { useState } from "react";
import type { Skill, Theme } from "./skill-themes";

export type Finding = {
  status: string;
  text: string;
  job_ids: string[];
  examples?: {
    job_id: string;
    snapshot_id: string;
    quote: string;
    period: string;
  }[];
};
export type Week = {
  from: string;
  to: string;
  eligible: number;
  parsed: number;
  coverage: number | null;
  complete: boolean;
  missing_days: string[];
  capped: boolean;
  extraction_versions: string[];
  skills: Skill[];
  themes: Theme[];
};
export type Comparison = {
  status: string;
  reasons: string[];
  prevalence_status: string;
  prevalence_reasons: string[];
  previous: Week;
  current: Week;
  volume: { change: number | null; relative_change: number | null };
};
const percent = (value: number | null) =>
  value === null ? "Unknown" : `${(value * 100).toFixed(0)}%`;
const skillKey = (s: Skill) =>
  JSON.stringify([s.skill, s.context, s.requiredness, s.alternative]);

export function Findings({
  findings,
  openJob,
}: {
  findings: Finding[];
  openJob: (id: string) => Promise<void>;
}) {
  return findings.map((finding, i) => (
    <article className="finding" key={i}>
      <span className="tag">{finding.status.replaceAll("_", " ")}</span>
      <p>{finding.text}</p>
      {!!finding.examples?.length && (
        <details>
          <summary>Read supporting excerpts from these weeks</summary>
          {finding.examples.map((e, n) => (
            <blockquote key={e.snapshot_id + n}>
              <small>
                {e.period === "current"
                  ? "Latest complete week"
                  : "Previous week"}
              </small>
              “{e.quote}”
              <br />
              <button className="textbutton" onClick={() => openJob(e.job_id)}>
                Inspect job {e.job_id}
              </button>
            </blockquote>
          ))}
        </details>
      )}
      {!finding.examples?.length &&
        finding.job_ids.map((id) => (
          <button className="textbutton" key={id} onClick={() => openJob(id)}>
            Inspect job {id}
          </button>
        ))}
    </article>
  ));
}

export function WeekComparison({ comparison }: { comparison: Comparison }) {
  const { previous, current, volume } = comparison;
  return (
    <>
      <h2>Complete-week comparison</h2>
      <p>
        {current.from} to {current.to} versus {previous.from} to {previous.to}.
        Monday–Sunday weeks in the monitor timezone; the current week is
        excluded.
      </p>
      <div className="tablewrap">
        <table>
          <caption>Distinct jobs observed in each week</caption>
          <thead>
            <tr>
              <th scope="col">Measure</th>
              <th scope="col">Previous week</th>
              <th scope="col">Latest complete week</th>
              <th scope="col">Change</th>
            </tr>
          </thead>
          <tbody>
            <tr>
              <th scope="row">Observed jobs</th>
              <td>{previous.eligible}</td>
              <td>{current.eligible}</td>
              <td>
                {volume.change === null
                  ? "Unavailable"
                  : `${volume.change > 0 ? "+" : ""}${volume.change} (${percent(volume.relative_change)})`}
              </td>
            </tr>
            <tr>
              <th scope="row">Parsed / observed</th>
              <td>
                {previous.parsed} / {previous.eligible} (
                {percent(previous.coverage)})
              </td>
              <td>
                {current.parsed} / {current.eligible} (
                {percent(current.coverage)})
              </td>
              <td>
                {comparison.prevalence_status === "ready"
                  ? "Comparable extraction"
                  : "Comparison unavailable"}
              </td>
            </tr>
          </tbody>
        </table>
      </div>
      {!!comparison.prevalence_reasons.length && (
        <div className="quality">
          <strong>More comparable data needed</strong>
          <ul>
            {comparison.prevalence_reasons.map((reason) => (
              <li key={reason}>{reason}</li>
            ))}
          </ul>
        </div>
      )}
      <p className="chartnote">
        Jobs are deduplicated within each week. The same job can be observed in
        both weeks. These counts describe the collected sample, not total
        hiring.
      </p>
    </>
  );
}

export function WeeklyHistory({ weeks }: { weeks: Week[] }) {
  const [selected, setSelected] = useState("theme:ai_engineering");
  const themes = new Map(
    weeks.flatMap((w) => w.themes.map((t) => [t.theme, t.label] as const)),
  );
  const skills = new Map(
    weeks.flatMap((w) => w.skills.map((s) => [skillKey(s), s] as const)),
  );
  const options = [
    ...Array.from(themes, ([key, label]) => ({ key: `theme:${key}`, label })),
    ...Array.from(skills, ([key, s]) => ({
      key: `skill:${key}`,
      label: `${s.skill} · ${s.context.replaceAll("_", " ")} · ${s.requiredness}${s.alternative ? " · alternative" : ""}`,
    })),
  ];
  const active = options.find((o) => o.key === selected) || options[0];
  const values = weeks.map((week) => {
    const row = active?.key.startsWith("theme:")
      ? week.themes.find((t) => `theme:${t.theme}` === active.key)
      : week.skills.find((s) => `skill:${skillKey(s)}` === active?.key);
    const share =
      week.complete && week.parsed > 0 && active
        ? (row?.count || 0) / week.parsed
        : null;
    return { week, row, share };
  });
  return (
    <section className="weeklyhistory">
      <h2>Skill and theme history</h2>
      <p>
        {weeks.length} finished calendar {weeks.length === 1 ? "week" : "weeks"}
        , ending {weeks.at(-1)?.to}. Shares use that week’s parsed descriptions.
        Missing collection days create gaps.
      </p>
      {active ? (
        <>
          <label>
            Skill or capability theme
            <select
              value={active.key}
              onChange={(e) => setSelected(e.target.value)}
            >
              <optgroup label="Capability themes">
                {options
                  .filter((o) => o.key.startsWith("theme:"))
                  .map((o) => (
                    <option key={o.key} value={o.key}>
                      {o.label}
                    </option>
                  ))}
              </optgroup>
              <optgroup label="Skills and interpretation">
                {options
                  .filter((o) => o.key.startsWith("skill:"))
                  .map((o) => (
                    <option key={o.key} value={o.key}>
                      {o.label}
                    </option>
                  ))}
              </optgroup>
            </select>
          </label>
          <div className="weekbars" aria-hidden="true">
            {values.map(({ week, share }) => (
              <div className="weekbar" key={week.from}>
                <span>{share === null ? "Gap" : percent(share)}</span>
                <div className="weekbar-track">
                  {share !== null && (
                    <div
                      className="bar"
                      style={{ height: `${share * 100}%` }}
                    />
                  )}
                </div>
                <small>
                  {week.from.slice(5)}–{week.to.slice(5)}
                </small>
              </div>
            ))}
          </div>
        </>
      ) : (
        <p>
          No extracted skills in these weeks yet. Complete collections and
          parsed descriptions will populate the history.
        </p>
      )}
      <div className="tablewrap">
        <table>
          <caption>{active?.label || "Weekly collection coverage"}</caption>
          <thead>
            <tr>
              <th scope="col">Week</th>
              <th scope="col">Collection</th>
              <th scope="col">Parsed / observed</th>
              <th scope="col">Jobs with evidence</th>
              <th scope="col">Share of parsed jobs</th>
            </tr>
          </thead>
          <tbody>
            {values.map(({ week, row, share }) => (
              <tr key={week.from}>
                <th scope="row">
                  {week.from} to {week.to}
                </th>
                <td>
                  {week.complete
                    ? "All 7 days collected"
                    : `${week.missing_days.length} missing days`}
                  {week.capped && <small>Result cap reached</small>}
                  {week.parsed > 0 && week.parsed < 30 && (
                    <small>Small parsed sample · N &lt; 30</small>
                  )}
                  {week.extraction_versions.length > 1 && (
                    <small>Mixed extraction versions</small>
                  )}
                </td>
                <td>
                  {week.parsed} / {week.eligible} ({percent(week.coverage)})
                </td>
                <td>
                  {share === null
                    ? "Unavailable"
                    : `${row?.count || 0} / ${week.parsed}`}
                </td>
                <td>{share === null ? "Gap" : percent(share)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="chartnote">
        Themes count each job once. Skill history keeps requiredness, context
        and alternatives separate. Zero means no supported mention among parsed
        jobs; it does not establish that employers do not require the skill.
        Weekly shares are descriptive; comparisons also require sufficient
        samples, matching extraction versions and similar coverage.
      </p>
    </section>
  );
}
