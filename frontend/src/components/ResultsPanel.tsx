import EmptyState from "./EmptyState";
import ResultCard from "./ResultCard";
import type { SearchResponse } from "../types";

interface ResultsPanelProps {
  error: string;
  loading: boolean;
  searchPayload: SearchResponse | null;
  yearLabel: string;
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
          {searchPayload.plan.used_fallback && <strong>Fallback parser</strong>}
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
