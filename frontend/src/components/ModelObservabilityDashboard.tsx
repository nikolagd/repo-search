import { Activity, BarChart3, BrainCircuit, Database, Gauge, Info, RefreshCw } from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";

import { fetchJson, getErrorMessage } from "../api/client";
import type {
  ModelObservabilityCard,
  ModelObservabilityResponse,
  ModelObservabilityWindow,
} from "../types";

const WINDOWS: ModelObservabilityWindow[] = ["15m", "1h", "6h", "24h", "7d", "15d"];

const CARD_TOOLTIPS: Record<string, string> = {
  Searches: "Total completed retrieval searches in the selected time window.",
  "Zero-result rate": "Share of searches that returned no final results in the selected time window.",
  "Fallback parser rate": "Share of searches where the query parser used fallback parsing instead of the normal LLM parser path.",
  "p95 search latency": "95th percentile total retrieval latency. 95% of completed searches were at or below this duration.",
  "Embedding coverage": "Share of indexed publications that currently have stored embeddings.",
};

const FIELD_TOOLTIPS: Record<string, string> = {
  "Embedding model": "Embedding model used to convert search queries and publications into vectors.",
  "Embedding device": "Runtime device used by the embedding service, for example CPU or CUDA.",
  "Embedding dimension": "Number of numeric dimensions in each embedding vector.",
  "LLM provider": "Provider used by the query parser service for natural-language query planning.",
  "LLM model": "LLM model configured for query parsing.",
  "Average results": "Average number of final ranked results returned per completed search in the selected window.",
  "Average candidates": "Average number of vector database candidates fetched before merge and ranking.",
  "Embedding queries": "Average number of semantic embedding queries generated from one user search.",
  "Average top score": "Average highest ranking score returned by each completed search. Zero-result searches contribute 0.",
  "Average score": "Average of the per-search average result score. Zero-result searches contribute 0.",
  Indexed: "Total number of publications currently stored in the application index.",
  "With embeddings": "Number of indexed publications that have an embedding vector.",
  Missing: "Number of indexed publications that do not yet have an embedding vector.",
  Coverage: "Share of indexed publications with embeddings.",
  Repository: "Repository name from which publications were harvested.",
  Publications: "Number of indexed publications for this repository.",
  "Query parse": "Time to turn the user query into filters and embedding query variants.",
  "Query embedding": "Time to convert parsed query variants into embedding vectors.",
  "Vector retrieval": "Time spent fetching nearest matches from PostgreSQL/pgvector.",
  "Candidate merge": "Time to merge candidates from multiple embedding queries.",
  Ranking: "Time to score, boost, sort, and trim final results.",
  Total: "Full search-service retrieval time for one request.",
  Llm: "Parser used the configured LLM path.",
  Fallback: "Parser used local fallback parsing.",
  "Fallback service error": "Parser used fallback parsing because the normal parser service path failed.",
};

function formatMetric(value: number, unit?: string): string {
  if (unit === "count") {
    return new Intl.NumberFormat("en", { maximumFractionDigits: 0 }).format(Math.round(value));
  }

  if (unit === "percent") {
    return `${(value * 100).toFixed(1)}%`;
  }

  if (unit === "seconds") {
    if (value < 1) {
      return `${(value * 1000).toFixed(0)} ms`;
    }
    return `${value.toFixed(2)} s`;
  }

  if (value >= 1000) {
    return new Intl.NumberFormat("en", { maximumFractionDigits: 1, notation: "compact" }).format(value);
  }

  const roundedToTwoDecimals = Math.round((value + Number.EPSILON) * 100) / 100;
  return roundedToTwoDecimals.toFixed(roundedToTwoDecimals % 1 === 0 ? 0 : 2);
}

function formatKey(value: string): string {
  return value
    .replaceAll("_", " ")
    .replace(/\b\w/g, (letter) => letter.toUpperCase());
}

function tooltipFor(label: string, fallback?: string): string {
  const normalizedLabel = label.toLowerCase();
  const fieldKey = Object.keys(FIELD_TOOLTIPS).find((key) => key.toLowerCase() === normalizedLabel);
  const cardKey = Object.keys(CARD_TOOLTIPS).find((key) => key.toLowerCase() === normalizedLabel);

  if (fieldKey) {
    return FIELD_TOOLTIPS[fieldKey];
  }

  if (cardKey) {
    return CARD_TOOLTIPS[cardKey];
  }

  return fallback ?? label;
}

function MetricLabel({ children, tooltip }: { children: string; tooltip: string }) {
  return (
    <span className="metric-label" title={tooltip}>
      <span>{children}</span>
      <Info aria-hidden="true" size={13} />
    </span>
  );
}

function ObservabilityCard({ card }: { card: ModelObservabilityCard }) {
  return (
    <article className="model-card">
      <Gauge aria-hidden="true" size={19} />
      <div>
        <MetricLabel tooltip={tooltipFor(card.label)}>{card.label}</MetricLabel>
        <strong>{formatMetric(card.value, card.unit)}</strong>
      </div>
    </article>
  );
}

export default function ModelObservabilityDashboard() {
  const [windowValue, setWindowValue] = useState<ModelObservabilityWindow>("1h");
  const [data, setData] = useState<ModelObservabilityResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const loadData = useCallback(async () => {
    setLoading(true);
    setError("");

    try {
      const payload = await fetchJson<ModelObservabilityResponse>(
        `/api/admin/model-observability?window=${windowValue}`,
      );
      setData(payload);
    } catch (err) {
      setError(getErrorMessage(err, "Model observability data could not be loaded"));
      setData(null);
    } finally {
      setLoading(false);
    }
  }, [windowValue]);

  useEffect(() => {
    loadData();
  }, [loadData]);

  const maxStageLatency = useMemo(
    () => Math.max(...(data?.stage_latency.map((item) => item.p95_seconds) ?? [0]), 0.001),
    [data],
  );
  const maxParserCount = useMemo(
    () => Math.max(...(data?.parser_modes.map((item) => item.count) ?? [0]), 1),
    [data],
  );

  return (
    <section className="admin-shell">
      <div className="admin-header">
        <div>
          <span className="eyebrow">Model observability</span>
          <h2>Retrieval pipeline</h2>
        </div>
        <div className="admin-session">
          <div className="window-switch" aria-label="Metric window">
            {WINDOWS.map((item) => (
              <button
                className={item === windowValue ? "active" : ""}
                key={item}
                type="button"
                onClick={() => setWindowValue(item)}
              >
                {item}
              </button>
            ))}
          </div>
          <button className="secondary-action refresh-action" type="button" onClick={loadData} disabled={loading}>
            <RefreshCw aria-hidden="true" className={loading ? "spin" : ""} size={18} />
            Refresh
          </button>
        </div>
      </div>

      {error && (
        <div className="admin-message error">
          {error} <Link to="/admin/login">Log in as admin</Link>
        </div>
      )}

      {!data && !error && (
        <div className="empty-state">
          <RefreshCw aria-hidden="true" className="spin" size={24} />
          <span>Loading model observability...</span>
        </div>
      )}

      {data && (
        <>
          <div className="model-card-grid">
            {data.cards.map((card) => (
              <ObservabilityCard card={card} key={card.label} />
            ))}
          </div>

          <div className="model-grid two-columns">
            <article className="model-panel">
              <div className="model-panel-header">
                <BrainCircuit aria-hidden="true" size={20} />
                <h3>Model configuration</h3>
              </div>
              <dl className="model-definition-list">
                <div>
                  <dt><MetricLabel tooltip={tooltipFor("Embedding model")}>Embedding model</MetricLabel></dt>
                  <dd>{data.model_config.embedding_model || "-"}</dd>
                </div>
                <div>
                  <dt><MetricLabel tooltip={tooltipFor("Embedding device")}>Embedding device</MetricLabel></dt>
                  <dd>{data.model_config.embedding_device || "-"}</dd>
                </div>
                <div>
                  <dt><MetricLabel tooltip={tooltipFor("Embedding dimension")}>Embedding dimension</MetricLabel></dt>
                  <dd>{data.model_config.embedding_dimension ?? "-"}</dd>
                </div>
                <div>
                  <dt><MetricLabel tooltip={tooltipFor("LLM provider")}>LLM provider</MetricLabel></dt>
                  <dd>{data.model_config.llm_provider || "-"}</dd>
                </div>
                <div>
                  <dt><MetricLabel tooltip={tooltipFor("LLM model")}>LLM model</MetricLabel></dt>
                  <dd>{data.model_config.llm_model || "-"}</dd>
                </div>
              </dl>
            </article>

            <article className="model-panel">
              <div className="model-panel-header">
                <BarChart3 aria-hidden="true" size={20} />
                <h3>Retrieval output</h3>
              </div>
              <dl className="model-definition-list compact">
                <div>
                  <dt><MetricLabel tooltip={tooltipFor("Average results")}>Average results</MetricLabel></dt>
                  <dd>{formatMetric(data.retrieval_output.avg_result_count)}</dd>
                </div>
                <div>
                  <dt><MetricLabel tooltip={tooltipFor("Average candidates")}>Average candidates</MetricLabel></dt>
                  <dd>{formatMetric(data.retrieval_output.avg_candidates)}</dd>
                </div>
                <div>
                  <dt><MetricLabel tooltip={tooltipFor("Embedding queries")}>Embedding queries</MetricLabel></dt>
                  <dd>{formatMetric(data.retrieval_output.avg_embedding_queries)}</dd>
                </div>
                <div>
                  <dt><MetricLabel tooltip={tooltipFor("Average top score")}>Average top score</MetricLabel></dt>
                  <dd>{formatMetric(data.retrieval_output.avg_top_score)}</dd>
                </div>
                <div>
                  <dt><MetricLabel tooltip={tooltipFor("Average score")}>Average score</MetricLabel></dt>
                  <dd>{formatMetric(data.retrieval_output.avg_score)}</dd>
                </div>
              </dl>
            </article>
          </div>

          <div className="model-grid two-columns">
            <article className="model-panel">
              <div className="model-panel-header">
                <Activity aria-hidden="true" size={20} />
                <h3>Retrieval speed p95</h3>
              </div>
              <div className="metric-bars">
                {data.stage_latency.map((item) => (
                  <div className="metric-bar-row" key={item.stage}>
                    <MetricLabel tooltip={tooltipFor(formatKey(item.stage))}>{formatKey(item.stage)}</MetricLabel>
                    <div className="metric-bar-track">
                      <div style={{ width: `${Math.max(4, (item.p95_seconds / maxStageLatency) * 100)}%` }} />
                    </div>
                    <strong>{formatMetric(item.p95_seconds, "seconds")}</strong>
                  </div>
                ))}
                {!data.stage_latency.length && <p className="model-muted">Run a search to generate retrieval metrics.</p>}
              </div>
            </article>

            <article className="model-panel">
              <div className="model-panel-header">
                <Gauge aria-hidden="true" size={20} />
                <h3>Parser modes</h3>
              </div>
              <div className="metric-bars">
                {data.parser_modes.map((item) => (
                  <div className="metric-bar-row" key={item.parser_mode}>
                    <MetricLabel tooltip={tooltipFor(formatKey(item.parser_mode))}>{formatKey(item.parser_mode)}</MetricLabel>
                    <div className="metric-bar-track">
                      <div style={{ width: `${Math.max(4, (item.count / maxParserCount) * 100)}%` }} />
                    </div>
                    <strong>{formatMetric(item.count, "count")}</strong>
                  </div>
                ))}
                {!data.parser_modes.length && <p className="model-muted">Parser mode data appears after searches are parsed.</p>}
              </div>
            </article>
          </div>

          <article className="model-panel">
            <div className="model-panel-header">
              <Database aria-hidden="true" size={20} />
              <h3>Index health</h3>
            </div>
            <div className="index-summary">
              <span title={tooltipFor("Indexed")}>Indexed {formatMetric(data.index.indexed_publications, "count")}</span>
              <span title={tooltipFor("With embeddings")}>With embeddings {formatMetric(data.index.publications_with_embeddings, "count")}</span>
              <span title={tooltipFor("Missing")}>Missing {formatMetric(data.index.missing_embeddings, "count")}</span>
              <span title={tooltipFor("Coverage")}>Coverage {formatMetric(data.index.embedding_coverage_ratio, "percent")}</span>
            </div>
            <div className="model-table-wrap">
              <table className="model-table">
                <thead>
                  <tr>
                    <th title={tooltipFor("Repository")}>Repository</th>
                    <th title={tooltipFor("Publications")}>Publications</th>
                    <th title={tooltipFor("With embeddings")}>With embeddings</th>
                    <th title={tooltipFor("Missing")}>Missing</th>
                  </tr>
                </thead>
                <tbody>
                  {data.index.repositories.map((repository) => (
                    <tr key={repository.repository}>
                      <td>{repository.repository}</td>
                      <td>{formatMetric(repository.publications, "count")}</td>
                      <td>{formatMetric(repository.publications_with_embeddings, "count")}</td>
                      <td>{formatMetric(repository.missing_embeddings, "count")}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </article>
        </>
      )}
    </section>
  );
}
