"use client";
import { useState } from "react";
export type Skill = {
  skill: string;
  theme: string;
  theme_label: string;
  subtheme: string;
  context: string;
  requiredness: string;
  alternative: boolean;
  count: number;
  denominator: number;
  percentage: number | null;
  examples: { job_id: string; quote: string }[];
};
export type Theme = {
  theme: string;
  label: string;
  count: number;
  denominator: number;
  percentage: number | null;
  requirement_count: number;
  responsibility_count: number;
  stack_count: number;
  mentioned_count: number;
  skills: string[];
};
const label = (value: string) => value.replaceAll("_", " ");
const percent = (value: number | null) =>
  value === null ? "Unknown" : `${(value * 100).toFixed(0)}%`;
export default function SkillThemes({
  skills,
  themes,
  openJob,
}: {
  skills: Skill[];
  themes: Theme[];
  openJob: (id: string) => Promise<void>;
}) {
  const [selected, setSelected] = useState("all"),
    [context, setContext] = useState("all");
  const filtered = skills.filter(
    (s) =>
      (selected === "all" || s.theme === selected) &&
      (context === "all" || s.context === context),
  );
  return (
    <div>
      {themes.length > 0 && (
        <div className="tablewrap">
          <table>
            <caption>
              Capability themes · unique jobs with an evidenced mention
            </caption>
            <thead>
              <tr>
                <th>Theme</th>
                <th>Jobs / parsed</th>
                <th>Candidate requirements</th>
                <th>Responsibilities</th>
                <th>Employer stack</th>
              </tr>
            </thead>
            <tbody>
              {themes.map((t) => (
                <tr key={t.theme}>
                  <td>
                    <button
                      className="textbutton"
                      onClick={() => setSelected(t.theme)}
                    >
                      {t.label}
                    </button>
                  </td>
                  <td>
                    {t.count} / {t.denominator} ({percent(t.percentage)})
                  </td>
                  <td>{t.requirement_count}</td>
                  <td>{t.responsibility_count}</td>
                  <td>{t.stack_count}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <p className="chartnote">
        A job counts once per theme. Context counts can overlap. A mentioned
        tool or responsibility is not automatically a must-have.
      </p>
      <div className="filters">
        <label>
          Capability theme
          <select
            value={selected}
            onChange={(e) => setSelected(e.target.value)}
          >
            <option value="all">All themes</option>
            {themes.map((t) => (
              <option key={t.theme} value={t.theme}>
                {t.label}
              </option>
            ))}
          </select>
        </label>
        <label>
          Evidence context
          <select value={context} onChange={(e) => setContext(e.target.value)}>
            <option value="all">All contexts</option>
            <option value="candidate_requirement">
              Candidate requirements
            </option>
            <option value="responsibility">Responsibilities</option>
            <option value="current_stack">Employer stack</option>
            <option value="other">Other mentions</option>
          </select>
        </label>
      </div>
      <div className="tablewrap">
        <table>
          <thead>
            <tr>
              <th>Skill / capability</th>
              <th>Theme</th>
              <th>Interpretation</th>
              <th>Jobs / parsed</th>
              <th>Share</th>
              <th>Evidence</th>
            </tr>
          </thead>
          <tbody>
            {filtered.map((s, i) => (
              <tr key={s.skill + s.context + s.requiredness + i}>
                <td>
                  <strong>{s.skill}</strong>
                  <small>{label(s.subtheme)}</small>
                </td>
                <td>{s.theme_label}</td>
                <td>
                  {s.requiredness}
                  {s.alternative ? " · alternative" : ""}
                  <small>{label(s.context)}</small>
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
        {!filtered.length && (
          <p className="empty">
            No supported skills match these theme and context filters.
          </p>
        )}
      </div>
    </div>
  );
}
