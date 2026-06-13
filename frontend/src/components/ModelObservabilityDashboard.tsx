import { Activity, BarChart3, BrainCircuit, Database, Gauge, RefreshCw } from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";

import { fetchJson, getErrorMessage } from "../api/client";
import type {
  ModelObservabilityCard,
  ModelObservabilityResponse,
  ModelObservabilityWindow,
} from "../types";

const WINDOWS: ModelObservabilityWindow[] = ["15m", "1h", "6h", "24h"];

function formatMetric(value: number, unit?: string): string {
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

  return value.toFixed(value % 1 === 0 ? 0 : 2);
}

function formatKey(value: string): string {
  return value
    .replaceAll("_", " ")
    .replace(/\b\w/g, (letter) => letter.toUpperCase());
}

function ObservabilityCard({ card }: { card: ModelObservabilityCard }) {
  return (
    <article className="model-card">
      <Gauge aria-hidden="true" size={19} />
      <div>
        <span>{card.label}</span>
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
                  <dt>Embedding model</dt>
                  <dd>{data.model_config.embedding_model || "-"}</dd>
                </div>
                <div>
                  <dt>Embedding device</dt>
                  <dd>{data.model_config.embedding_device || "-"}</dd>
                </div>
                <div>
                  <dt>Embedding dimension</dt>
                  <dd>{data.model_config.embedding_dimension ?? "-"}</dd>
                </div>
                <div>
                  <dt>LLM provider</dt>
                  <dd>{data.model_config.llm_provider || "-"}</dd>
                </div>
                <div>
                  <dt>LLM model</dt>
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
                  <dt>Average results</dt>
                  <dd>{formatMetric(data.retrieval_output.avg_result_count)}</dd>
                </div>
                <div>
                  <dt>Average candidates</dt>
                  <dd>{formatMetric(data.retrieval_output.avg_candidates)}</dd>
                </div>
                <div>
                  <dt>Embedding queries</dt>
                  <dd>{formatMetric(data.retrieval_output.avg_embedding_queries)}</dd>
                </div>
                <div>
                  <dt>Average top score</dt>
                  <dd>{formatMetric(data.retrieval_output.avg_top_score)}</dd>
                </div>
                <div>
                  <dt>Average score</dt>
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
                    <span>{formatKey(item.stage)}</span>
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
                    <span>{formatKey(item.parser_mode)}</span>
                    <div className="metric-bar-track">
                      <div style={{ width: `${Math.max(4, (item.count / maxParserCount) * 100)}%` }} />
                    </div>
                    <strong>{formatMetric(item.count)}</strong>
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
              <span>Indexed {formatMetric(data.index.indexed_publications)}</span>
              <span>With embeddings {formatMetric(data.index.publications_with_embeddings)}</span>
              <span>Missing {formatMetric(data.index.missing_embeddings)}</span>
              <span>Coverage {formatMetric(data.index.embedding_coverage_ratio, "percent")}</span>
            </div>
            <div className="model-table-wrap">
              <table className="model-table">
                <thead>
                  <tr>
                    <th>Repository</th>
                    <th>Publications</th>
                    <th>With embeddings</th>
                    <th>Missing</th>
                  </tr>
                </thead>
                <tbody>
                  {data.index.repositories.map((repository) => (
                    <tr key={repository.repository}>
                      <td>{repository.repository}</td>
                      <td>{repository.publications}</td>
                      <td>{repository.publications_with_embeddings}</td>
                      <td>{repository.missing_embeddings}</td>
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
