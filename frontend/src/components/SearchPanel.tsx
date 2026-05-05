import type { FormEvent } from "react";
import { RefreshCw, Search } from "lucide-react";

interface SearchPanelProps {
  examples: string[];
  limit: number;
  loading: boolean;
  onLimitChange: (limit: number) => void;
  onQueryChange: (query: string) => void;
  onSubmit: (event: FormEvent<HTMLFormElement>) => void;
  query: string;
}

export default function SearchPanel({
  examples,
  limit,
  loading,
  onLimitChange,
  onQueryChange,
  onSubmit,
  query,
}: SearchPanelProps) {
  return (
    <form className="search-panel" onSubmit={onSubmit}>
      <label htmlFor="query">Search query</label>
      <textarea
        id="query"
        value={query}
        onChange={(event) => onQueryChange(event.target.value)}
        rows={4}
      />

      <div className="examples" aria-label="Example queries">
        {examples.map((example) => (
          <button
            key={example}
            type="button"
            onClick={() => onQueryChange(example)}
          >
            {example}
          </button>
        ))}
      </div>

      <div className="controls">
        <label htmlFor="limit">Results</label>
        <input
          id="limit"
          type="number"
          min="1"
          max="50"
          value={limit}
          onChange={(event) => onLimitChange(Number(event.target.value))}
        />
        <button className="primary-action" type="submit" disabled={loading || !query.trim()}>
          {loading ? <RefreshCw aria-hidden="true" className="spin" size={18} /> : <Search aria-hidden="true" size={18} />}
          Search
        </button>
      </div>
    </form>
  );
}
