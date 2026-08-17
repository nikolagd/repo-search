import EmptyState from "./EmptyState";
import ResultCard from "./ResultCard";
import type { SearchResponse } from "../types";

interface ResultsPanelProps {
  error: string;
  loading: boolean;
  searchPayload: SearchResponse | null;
  yearLabel: string;
}

const SEARCH_MODE_LABELS: Record<SearchResponse["search_mode"], string> = {
  semantic: "Semantic",
  author: "Author",
  hybrid: "Hybrid",
};

const PARSER_MODE_LABELS: Record<string, string> = {
  llm: "LLM",
  llm_repaired: "LLM repaired",
  fallback: "Fallback",
  fallback_service_error: "Fallback after service error",
  explicit: "Explicit filters",
};

function parserModeLabel(mode?: string): string {
  if (!mode) return "Unknown";
  return PARSER_MODE_LABELS[mode] ?? mode.replaceAll("_", " ");
}

export default function ResultsPanel({ error, loading, searchPayload, yearLabel }: ResultsPanelProps) {
  const results = searchPayload?.results ?? [];
  const resultCount = results.length;

  return (
    <section className="results-panel">
      <div className="results-header">
        <div>
          <span className="eyebrow">Results</span>
          <h2>{resultCount ? `${resultCount} matches` : "No active search"}</h2>
        </div>
        <span className="year-pill">{yearLabel}</span>
      </div>

      {searchPayload?.plan && (
        <div className="query-plan">
          <span>{searchPayload.plan.interpreted_query}</span>
          <div className="query-plan-modes">
            <strong>Retrieval: {SEARCH_MODE_LABELS[searchPayload.search_mode]}</strong>
            <strong>Parser: {parserModeLabel(searchPayload.plan.parser_mode)}</strong>
          </div>
        </div>
      )}

      {!results.length ? (
        <EmptyState loading={loading} error={error} />
      ) : (
        <div className="results-list">
          {results.map((result) => (
            <ResultCard result={result} key={result.id} />
          ))}
        </div>
      )}
    </section>
  );
}
