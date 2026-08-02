import { type FormEvent, type KeyboardEvent, useState } from "react";
import { Plus, RefreshCw, Search, X } from "lucide-react";

interface SearchPanelProps {
  examples: string[];
  authorNames: string[];
  limit: number;
  loading: boolean;
  onLimitChange: (limit: number) => void;
  onAuthorNamesChange: (authorNames: string[]) => void;
  onQueryChange: (query: string) => void;
  onSubmit: (event: FormEvent<HTMLFormElement>) => void;
  query: string;
}

export default function SearchPanel({
  examples,
  authorNames,
  limit,
  loading,
  onLimitChange,
  onAuthorNamesChange,
  onQueryChange,
  onSubmit,
  query,
}: SearchPanelProps) {
  const [authorDraft, setAuthorDraft] = useState("");

  function addAuthor() {
    const name = authorDraft.trim().replace(/\s+/g, " ");
    if (!name || name.length > 200 || authorNames.length >= 10) return;
    if (!authorNames.some((author) => author.toLocaleLowerCase() === name.toLocaleLowerCase())) {
      onAuthorNamesChange([...authorNames, name]);
    }
    setAuthorDraft("");
  }

  function handleAuthorKeyDown(event: KeyboardEvent<HTMLInputElement>) {
    if (event.key === "Enter") {
      event.preventDefault();
      addAuthor();
    }
  }

  return (
    <form className="search-panel" onSubmit={onSubmit}>
      <label htmlFor="query">Search query</label>
      <textarea
        id="query"
        value={query}
        onChange={(event) => onQueryChange(event.target.value)}
        rows={4}
      />

      <div className="author-filter">
        <label htmlFor="author-filter-input">Authors</label>
        <div className="author-entry">
          <input
            id="author-filter-input"
            maxLength={200}
            value={authorDraft}
            onChange={(event) => setAuthorDraft(event.target.value)}
            onKeyDown={handleAuthorKeyDown}
          />
          <button
            type="button"
            className="icon-action"
            disabled={!authorDraft.trim() || authorNames.length >= 10}
            onClick={addAuthor}
            title="Add author filter"
            aria-label="Add author filter"
          >
            <Plus aria-hidden="true" size={18} />
          </button>
        </div>
        {!!authorNames.length && (
          <div className="author-chips" aria-label="Selected author filters">
            {authorNames.map((author) => (
              <span className="author-chip" key={author.toLocaleLowerCase()}>
                {author}
                <button
                  type="button"
                  onClick={() => onAuthorNamesChange(authorNames.filter((item) => item !== author))}
                  title={`Remove ${author}`}
                  aria-label={`Remove author filter ${author}`}
                >
                  <X aria-hidden="true" size={14} />
                </button>
              </span>
            ))}
          </div>
        )}
      </div>

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
        <button className="primary-action" type="submit" disabled={loading || (!query.trim() && !authorNames.length)}>
          {loading ? <RefreshCw aria-hidden="true" className="spin" size={18} /> : <Search aria-hidden="true" size={18} />}
          Search
        </button>
      </div>
    </form>
  );
}
