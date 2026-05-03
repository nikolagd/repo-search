import { useEffect, useMemo, useState } from "react";
import {
  Activity,
  AlertCircle,
  ArrowUpRight,
  Database,
  RefreshCw,
  Search,
  Server,
} from "lucide-react";

const EXAMPLE_QUERIES = [
  "radovi o masinskom ucenju posle 2021",
  "find papers about digital transformation since 2020",
  "publikacije o informacionim sistemima koje pominju open data",
];

async function fetchJson(url, options) {
  const response = await fetch(url, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });

  const payload = await response.json().catch(() => ({}));

  if (!response.ok) {
    throw new Error(payload.detail || "Request failed");
  }

  return payload;
}

function formatDate(value) {
  if (!value) {
    return "No date";
  }

  return new Intl.DateTimeFormat("en", {
    year: "numeric",
    month: "short",
    day: "2-digit",
  }).format(new Date(value));
}

function formatScore(value) {
  return Number(value || 0).toFixed(3);
}

function Stat({ icon: Icon, label, value }) {
  return (
    <section className="stat">
      <Icon aria-hidden="true" size={18} />
      <div>
        <span>{label}</span>
        <strong>{value}</strong>
      </div>
    </section>
  );
}

function EmptyState({ loading, error }) {
  if (loading) {
    return (
      <div className="empty-state">
        <RefreshCw aria-hidden="true" className="spin" size={24} />
        <span>Searching publications...</span>
      </div>
    );
  }

  if (error) {
    return (
      <div className="empty-state error">
        <AlertCircle aria-hidden="true" size={24} />
        <span>{error}</span>
      </div>
    );
  }

  return (
    <div className="empty-state">
      <Search aria-hidden="true" size={24} />
      <span>Search results will appear here.</span>
    </div>
  );
}

export default function App() {
  const [query, setQuery] = useState(EXAMPLE_QUERIES[0]);
  const [limit, setLimit] = useState(10);
  const [health, setHealth] = useState(null);
  const [stats, setStats] = useState(null);
  const [repositories, setRepositories] = useState([]);
  const [searchPayload, setSearchPayload] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  const resultCount = searchPayload?.results?.length ?? 0;
  const yearLabel = useMemo(() => {
    const plan = searchPayload?.plan;

    if (!plan) {
      return "Any year";
    }

    if (plan.year_from && plan.year_to) {
      return `${plan.year_from}-${plan.year_to}`;
    }

    if (plan.year_from) {
      return `From ${plan.year_from}`;
    }

    if (plan.year_to) {
      return `Until ${plan.year_to}`;
    }

    return "Any year";
  }, [searchPayload]);

  useEffect(() => {
    let ignore = false;

    async function loadOverview() {
      const [healthData, statsData, repoData] = await Promise.allSettled([
        fetchJson("/api/health"),
        fetchJson("/api/stats"),
        fetchJson("/api/repositories"),
      ]);

      if (ignore) {
        return;
      }

      if (healthData.status === "fulfilled") {
        setHealth(healthData.value);
      }

      if (statsData.status === "fulfilled") {
        setStats(statsData.value);
      }

      if (repoData.status === "fulfilled") {
        setRepositories(repoData.value);
      }
    }

    loadOverview();
    return () => {
      ignore = true;
    };
  }, []);

  async function submitSearch(event) {
    event?.preventDefault();

    setLoading(true);
    setError("");

    try {
      const payload = await fetchJson("/api/search", {
        method: "POST",
        body: JSON.stringify({ query, limit }),
      });
      setSearchPayload(payload);
    } catch (err) {
      setError(err.message);
      setSearchPayload(null);
    } finally {
      setLoading(false);
    }
  }

  return (
    <main className="app-shell">
      <header className="topbar">
        <div>
          <span className="eyebrow">Repository intelligence</span>
          <h1>Repo Search</h1>
        </div>
        <div className="system-status" title="API and database status">
          <Server aria-hidden="true" size={18} />
          <span>API {health?.status || "..."}</span>
          <span className={health?.database === "ok" ? "dot ok" : "dot"} />
          <span>DB {health?.database || "..."}</span>
        </div>
      </header>

      <section className="overview">
        <Stat icon={Database} label="Repositories" value={stats?.repositories ?? repositories.length ?? "-"} />
        <Stat icon={Activity} label="Publications" value={stats?.publications ?? "-"} />
        <Stat icon={Search} label="Embedded" value={stats?.publications_with_embeddings ?? "-"} />
        <Stat icon={RefreshCw} label="Last publication" value={formatDate(stats?.last_harvest)} />
      </section>

      <section className="workspace">
        <form className="search-panel" onSubmit={submitSearch}>
          <label htmlFor="query">Search query</label>
          <textarea
            id="query"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            rows={4}
          />

          <div className="examples" aria-label="Example queries">
            {EXAMPLE_QUERIES.map((example) => (
              <button
                key={example}
                type="button"
                onClick={() => setQuery(example)}
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
              onChange={(event) => setLimit(Number(event.target.value))}
            />
            <button className="primary-action" type="submit" disabled={loading || !query.trim()}>
              {loading ? <RefreshCw aria-hidden="true" className="spin" size={18} /> : <Search aria-hidden="true" size={18} />}
              Search
            </button>
          </div>
        </form>

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

          {!searchPayload?.results?.length ? (
            <EmptyState loading={loading} error={error} />
          ) : (
            <div className="results-list">
              {searchPayload.results.map((result) => (
                <article className="result-card" key={result.id}>
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
              ))}
            </div>
          )}
        </section>
      </section>
    </main>
  );
}
