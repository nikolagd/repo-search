import { type FormEvent, type KeyboardEvent, useEffect, useState } from "react";
import { Plus, RefreshCw, Search, X } from "lucide-react";

import { fetchJson } from "../api/client";
import type { AuthorFilter, AuthorMatch, AuthorSuggestion, AuthorSuggestionsResponse } from "../types";

interface SearchPanelProps {
  examples: string[];
  authorFilters: AuthorFilter[];
  authorMatch: AuthorMatch;
  limit: number;
  loading: boolean;
  onLimitChange: (limit: number) => void;
  onAuthorFiltersChange: (authors: AuthorFilter[]) => void;
  onAuthorMatchChange: (match: AuthorMatch) => void;
  onQueryChange: (query: string) => void;
  onSubmit: (event: FormEvent<HTMLFormElement>) => void;
  query: string;
}

export default function SearchPanel({
  examples,
  authorFilters,
  authorMatch,
  limit,
  loading,
  onLimitChange,
  onAuthorFiltersChange,
  onAuthorMatchChange,
  onQueryChange,
  onSubmit,
  query,
}: SearchPanelProps) {
  const [authorDraft, setAuthorDraft] = useState("");
  const [suggestions, setSuggestions] = useState<AuthorSuggestion[]>([]);
  const [suggestionsLoading, setSuggestionsLoading] = useState(false);

  useEffect(() => {
    const query = authorDraft.trim();
    if (query.replace(/[^\p{L}\p{N}]/gu, "").length < 2) {
      setSuggestions([]);
      setSuggestionsLoading(false);
      return;
    }

    const controller = new AbortController();
    const timeout = window.setTimeout(async () => {
      setSuggestionsLoading(true);
      try {
        const response = await fetchJson<AuthorSuggestionsResponse>(
          `/api/authors/suggestions?q=${encodeURIComponent(query)}&limit=8`,
          { signal: controller.signal },
        );
        setSuggestions(response.suggestions);
      } catch (error) {
        if (!(error instanceof DOMException && error.name === "AbortError")) setSuggestions([]);
      } finally {
        if (!controller.signal.aborted) setSuggestionsLoading(false);
      }
    }, 200);

    return () => {
      window.clearTimeout(timeout);
      controller.abort();
    };
  }, [authorDraft]);

  function addAuthor() {
    const name = authorDraft.trim().replace(/\s+/g, " ");
    if (!name || name.length > 200 || authorFilters.length >= 10) return;
    if (!authorFilters.some((author) => author.display_name.toLocaleLowerCase() === name.toLocaleLowerCase())) {
      onAuthorFiltersChange([...authorFilters, { id: null, display_name: name, source: "manual" }]);
    }
    setAuthorDraft("");
    setSuggestions([]);
  }

  function selectSuggestion(suggestion: AuthorSuggestion) {
    if (authorFilters.length >= 10 || authorFilters.some((author) => author.id === suggestion.id)) return;
    onAuthorFiltersChange([
      ...authorFilters,
      { id: suggestion.id, display_name: suggestion.display_name, source: "manual" },
    ]);
    setAuthorDraft("");
    setSuggestions([]);
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
            disabled={!authorDraft.trim() || authorFilters.length >= 10}
            onClick={addAuthor}
            title="Add author filter"
            aria-label="Add author filter"
          >
            <Plus aria-hidden="true" size={18} />
          </button>
        </div>
        {(suggestionsLoading || suggestions.length > 0) && (
          <div className="author-suggestions" role="listbox" aria-label="Author suggestions">
            {suggestionsLoading && <span className="author-suggestion-status">Finding authors…</span>}
            {!suggestionsLoading && suggestions.map((suggestion) => (
              <button
                type="button"
                role="option"
                aria-selected="false"
                key={suggestion.id}
                onClick={() => selectSuggestion(suggestion)}
              >
                <span>{suggestion.display_name}</span>
                <small>{suggestion.publication_count} publications</small>
              </button>
            ))}
          </div>
        )}
        <small className="author-help">Choose a suggestion to use its exact author record, or add the typed name for deterministic matching.</small>
        {!!authorFilters.length && (
          <div className="author-chips" aria-label="Selected author filters">
            {authorFilters.map((author) => (
              <span className="author-chip" key={`${author.source}:${author.id ?? author.display_name.toLocaleLowerCase()}`}>
                {author.display_name}
                {author.source === "query" && <small>query</small>}
                <button
                  type="button"
                  onClick={() => onAuthorFiltersChange(authorFilters.filter((item) => item !== author))}
                  title={`Remove ${author.display_name}`}
                  aria-label={`Remove author filter ${author.display_name}`}
                >
                  <X aria-hidden="true" size={14} />
                </button>
              </span>
            ))}
          </div>
        )}
        {authorFilters.length >= 2 && (
          <div className="author-match" role="group" aria-label="Author match mode">
            <button
              type="button"
              aria-pressed={authorMatch === "any"}
              className={authorMatch === "any" ? "active" : ""}
              onClick={() => onAuthorMatchChange("any")}
            >
              Any author
            </button>
            <button
              type="button"
              aria-pressed={authorMatch === "all"}
              className={authorMatch === "all" ? "active" : ""}
              onClick={() => onAuthorMatchChange("all")}
            >
              All authors
            </button>
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
        <button className="primary-action" type="submit" disabled={loading || (!query.trim() && !authorFilters.length)}>
          {loading ? <RefreshCw aria-hidden="true" className="spin" size={18} /> : <Search aria-hidden="true" size={18} />}
          Search
        </button>
      </div>
    </form>
  );
}
