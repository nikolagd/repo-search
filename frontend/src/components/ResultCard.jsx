import { ArrowUpRight } from "lucide-react";

import { formatDate, formatScore } from "../utils/format";

export default function ResultCard({ result }) {
  return (
    <article className="result-card">
      <div className="result-score">
        <span>{formatScore(result.score)}</span>
        <small>score</small>
      </div>
      <div className="result-main">
        <div className="result-meta">
          <span>{formatDate(result.date)}</span>
          {result.repository && <span>{result.repository}</span>}
          <span>Matched: {result.matched_query}</span>
        </div>
        <h3>{result.title || "Untitled publication"}</h3>
        {!!result.authors?.length && (
          <div className="authors">{result.authors.slice(0, 4).join(", ")}</div>
        )}
        {result.abstract && <p>{result.abstract}</p>}
        <div className="boosts">
          <span>Similarity {formatScore(result.cosine_similarity)}</span>
          <span>Topic {formatScore(result.topic_boost)}</span>
          <span>Coverage {formatScore(result.coverage_boost)}</span>
        </div>
      </div>
      {result.source_url && (
        <a className="open-link" href={result.source_url} target="_blank" rel="noreferrer" title="Open source">
          <ArrowUpRight aria-hidden="true" size={18} />
        </a>
      )}
    </article>
  );
}
