"use client";
import { useState } from "react";

type Requirement = {
  canonical_candidate: string;
  category: string;
  theme: string;
  subtheme: string;
  requiredness: string;
  context: string;
  alternatives_group: string | null;
  explicit_negation: boolean;
  evidence_quote: string;
  confidence: number;
};
type Pay = {
  type: string;
  min_amount: number | null;
  max_amount: number | null;
  currency_iso: string | null;
  period: string;
  evidence_quote: string;
};
type Extraction = {
  seniority: string;
  seniority_basis: string;
  work_arrangement: string;
  remote_geo_restriction: string | null;
  contract_type: string;
  minimum_experience_years: number | null;
  degree_requirement: string;
  requirements: Requirement[];
  compensation: Pay[];
  extraction_warnings: string[];
};
export type JobDetail = {
  id: string;
  title: string;
  description: string | null;
  source_available: boolean;
  extraction: Extraction | null;
  extraction_status: string;
  extraction_version: string | null;
  processing_status: string;
  selection: { source_chars?: number; selected_chars?: number };
  processing_diagnostics?: {
    model?: string;
    duration_ms?: number;
    http_status?: number;
    provider_request_id?: string;
    call_id?: string;
    validation_status?: string;
    failure_code?: string;
    failure_stage?: string;
    usage?: {
      prompt_tokens?: number;
      completion_tokens?: number;
      total_tokens?: number;
    };
  };
};
const label = (value: string) => value.replaceAll("_", " ");
export default function JobEvidence({
  detail,
  loadSource,
}: {
  detail: JobDetail;
  loadSource: () => Promise<string>;
}) {
  const [source, setSource] = useState<string | null>(null),
    [loading, setLoading] = useState(false),
    [error, setError] = useState("");
  const data = detail.extraction;
  const groups = data
    ? [...new Set(data.requirements.map((r) => r.theme || "other"))]
    : [];
  const themeLabels: Record<string, string> = {
    ai_engineering: "AI engineering",
    api_and_system_design: "API and system design",
    languages_and_frameworks: "Languages and frameworks",
    cloud_and_delivery: "Cloud and delivery",
    data_and_messaging: "Data and messaging",
    quality_and_observability: "Quality and observability",
    security: "Security",
    collaboration: "Collaboration and leadership",
    other: "Other skills",
  };
  return (
    <article className="evidence">
      <h3>{detail.title || "Job evidence"}</h3>
      <p>
        {detail.extraction_version?.startsWith("llm:")
          ? "LLM classification with checked source quotes"
          : "Rules-based classification"}
        {detail.processing_status === "quarantined"
          ? " · Latest processing attempt needs review; showing the last valid result."
          : ""}
      </p>
      {(detail.processing_diagnostics?.call_id ||
        detail.processing_diagnostics?.model) && (
        <details>
          <summary>AI processing audit</summary>
          <dl className="jobfacts">
            {Object.entries({
              Model: detail.processing_diagnostics.model,
              "HTTP status": detail.processing_diagnostics.http_status,
              "Duration (ms)": detail.processing_diagnostics.duration_ms,
              Validation: detail.processing_diagnostics.validation_status,
              "Failure stage": detail.processing_diagnostics.failure_stage,
              "Failure reason": detail.processing_diagnostics.failure_code,
              "Total tokens": detail.processing_diagnostics.usage?.total_tokens,
              "Call ID": detail.processing_diagnostics.call_id,
              "Provider request ID":
                detail.processing_diagnostics.provider_request_id,
            })
              .filter(([, value]) => value !== undefined && value !== null)
              .map(([key, value]) => (
                <div key={key}>
                  <dt>{key}</dt>
                  <dd>{String(value)}</dd>
                </div>
              ))}
          </dl>
        </details>
      )}
      {data ? (
        <>
          <dl className="jobfacts">
            <div>
              <dt>Seniority</dt>
              <dd>{label(data.seniority)}</dd>
            </div>
            <div>
              <dt>Work arrangement</dt>
              <dd>
                {label(data.work_arrangement)}
                {data.remote_geo_restriction
                  ? ` · ${data.remote_geo_restriction}`
                  : ""}
              </dd>
            </div>
            <div>
              <dt>Contract</dt>
              <dd>{label(data.contract_type)}</dd>
            </div>
            <div>
              <dt>Minimum experience</dt>
              <dd>
                {data.minimum_experience_years === null
                  ? "Not stated"
                  : `${data.minimum_experience_years} years`}
              </dd>
            </div>
            <div>
              <dt>Degree</dt>
              <dd>{label(data.degree_requirement)}</dd>
            </div>
          </dl>
          {groups.map((group) => (
            <section key={group}>
              <h4>{themeLabels[group] || label(group)}</h4>
              <div className="tablewrap">
                <table>
                  <thead>
                    <tr>
                      <th>Requirement</th>
                      <th>Category</th>
                      <th>Source evidence</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.requirements
                      .filter((r) => (r.theme || "other") === group)
                      .map((r, i) => (
                        <tr key={r.canonical_candidate + i}>
                          <td>
                            <strong>{r.canonical_candidate}</strong>
                            {r.alternatives_group && (
                              <small>
                                Alternative · {r.alternatives_group}
                              </small>
                            )}
                            {r.confidence < 0.7 && (
                              <small>Low confidence</small>
                            )}
                          </td>
                          <td>
                            {label(r.category)}
                            <small>{label(r.subtheme || "general")}</small>
                            <small>
                              {r.explicit_negation
                                ? "Explicitly not required"
                                : label(r.requiredness)}{" "}
                              · {label(r.context)}
                            </small>
                          </td>
                          <td>
                            <details>
                              <summary>Read short quote</summary>
                              <blockquote>{r.evidence_quote}</blockquote>
                            </details>
                          </td>
                        </tr>
                      ))}
                  </tbody>
                </table>
              </div>
            </section>
          ))}
          {!groups.length && (
            <p>No supported requirements have been extracted yet.</p>
          )}
          <h4>Advertised compensation</h4>
          {data.compensation.length ? (
            data.compensation.map((pay, i) => (
              <div key={i}>
                <p>
                  {pay.currency_iso || "Currency unknown"}{" "}
                  {pay.min_amount?.toLocaleString("en-GB") ??
                    "Lower bound unknown"}{" "}
                  –{" "}
                  {pay.max_amount?.toLocaleString("en-GB") ??
                    "Upper bound unknown"}{" "}
                  / {pay.period} · {label(pay.type)}
                </p>
                <details>
                  <summary>Read pay evidence</summary>
                  <blockquote>{pay.evidence_quote}</blockquote>
                </details>
              </div>
            ))
          ) : (
            <p>No verified advertised pay extracted.</p>
          )}
          {data.extraction_warnings.length > 0 && (
            <details>
              <summary>Processing caveats</summary>
              <ul>
                {data.extraction_warnings.map((warning, i) => (
                  <li key={i}>{warning}</li>
                ))}
              </ul>
            </details>
          )}
        </>
      ) : (
        <p>
          {detail.source_available
            ? "Description saved; no valid extraction yet."
            : "No description was retrieved for this listing."}
        </p>
      )}
      {detail.selection.selected_chars !== undefined && (
        <p className="chartnote">
          {detail.selection.selected_chars.toLocaleString("en-GB")} of{" "}
          {detail.selection.source_chars?.toLocaleString("en-GB")} source
          characters selected for classification.
        </p>
      )}
      {detail.source_available && (
        <details
          onToggle={async (e) => {
            if (!e.currentTarget.open || source !== null || loading) return;
            setLoading(true);
            setError("");
            try {
              setSource(await loadSource());
            } catch {
              setError(
                "Could not load source text. Close and reopen to retry.",
              );
            } finally {
              setLoading(false);
            }
          }}
        >
          <summary>Inspect original source description</summary>
          {loading ? (
            <p role="status">Loading source…</p>
          ) : error ? (
            <p role="alert">{error}</p>
          ) : (
            <p className="description">{source}</p>
          )}
        </details>
      )}
    </article>
  );
}
