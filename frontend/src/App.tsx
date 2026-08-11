import { type FormEvent, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Navigate, Route, Routes } from "react-router-dom";

import { fetchJson, getErrorMessage } from "./api/client";
import AdminPanel from "./components/AdminPanel";
import ModelObservabilityDashboard from "./components/ModelObservabilityDashboard";
import OverviewStats from "./components/OverviewStats";
import ResultsPanel from "./components/ResultsPanel";
import SearchPanel from "./components/SearchPanel";
import Topbar from "./components/Topbar";
import { EXAMPLE_QUERIES } from "./constants/searchExamples";
import type { AuthorFilter, AuthorMatch, HealthResponse, RepositoryResponse, SearchResponse, StatsResponse } from "./types";

export default function App() {
  const [query, setQuery] = useState(EXAMPLE_QUERIES[0]);
  const [limit, setLimit] = useState(10);
  const [authorFilters, setAuthorFilters] = useState<AuthorFilter[]>([]);
  const [authorMatch, setAuthorMatch] = useState<AuthorMatch>("any");
  const [authorMatchOverride, setAuthorMatchOverride] = useState<AuthorMatch | null>(null);
  const [health, setHealth] = useState<HealthResponse | null>(null);
  const [stats, setStats] = useState<StatsResponse | null>(null);
  const [repositories, setRepositories] = useState<RepositoryResponse[]>([]);
  const [searchPayload, setSearchPayload] = useState<SearchResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const requestGeneration = useRef(0);

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

  const loadOverview = useCallback(async () => {
    const [healthData, statsData, repoData] = await Promise.allSettled([
      fetchJson<HealthResponse>("/api/health"),
      fetchJson<StatsResponse>("/api/stats"),
      fetchJson<RepositoryResponse[]>("/api/repositories"),
    ]);

    if (healthData.status === "fulfilled") {
      setHealth(healthData.value);
    }

    if (statsData.status === "fulfilled") {
      setStats(statsData.value);
    }

    if (repoData.status === "fulfilled") {
      setRepositories(repoData.value);
    }
  }, []);

  useEffect(() => {
    loadOverview();
  }, [loadOverview]);

  async function submitSearch(event?: FormEvent<HTMLFormElement>) {
    event?.preventDefault();

    const generation = ++requestGeneration.current;
    const submittedQuery = query;
    const manualAuthors = authorFilters.filter((author) => author.source === "manual");
    setLoading(true);
    setError("");

    try {
      const payload = await fetchJson<SearchResponse>("/api/search", {
        method: "POST",
        body: JSON.stringify({
          query,
          author_names: manualAuthors.filter((author) => author.id === null).map((author) => author.display_name),
          author_ids: manualAuthors.flatMap((author) => author.id === null ? [] : [author.id]),
          ...(authorMatchOverride === null ? {} : { author_match: authorMatchOverride }),
          limit,
        }),
      });
      if (generation !== requestGeneration.current || submittedQuery !== query) return;
      setSearchPayload(payload);
      setAuthorFilters((current) => {
        const manual = current.filter((author) => author.source === "manual");
        const manualNames = new Set(manual.map((author) => author.display_name.trim().toLocaleLowerCase()));
        const queryAuthors = payload.plan.extracted_author_names
          .filter((name) => !manualNames.has(name.trim().toLocaleLowerCase()))
          .slice(0, Math.max(0, 10 - manual.length))
          .map((display_name) => ({ id: null, display_name, source: "query" as const }));
        return [...manual, ...queryAuthors];
      });
      setAuthorMatch(payload.plan.author_match);
    } catch (err) {
      if (generation === requestGeneration.current) {
        setError(getErrorMessage(err, "Search failed"));
        setSearchPayload(null);
      }
    } finally {
      if (generation === requestGeneration.current) setLoading(false);
    }
  }

  function handleQueryChange(nextQuery: string) {
    requestGeneration.current += 1;
    setLoading(false);
    setQuery(nextQuery);
    setAuthorFilters((current) => current.filter((author) => author.source === "manual"));
    setSearchPayload(null);
    if (authorMatchOverride === null) setAuthorMatch("any");
  }

  function handleAuthorFiltersChange(authors: AuthorFilter[]) {
    requestGeneration.current += 1;
    setLoading(false);
    if (authorFilters.length < 2 && authors.length >= 2) {
      setAuthorMatch("any");
      setAuthorMatchOverride(null);
    }
    setAuthorFilters(authors);
  }

  function handleAuthorMatchChange(match: AuthorMatch) {
    requestGeneration.current += 1;
    setLoading(false);
    setAuthorMatch(match);
    setAuthorMatchOverride(match);
  }

  const searchPage = (
    <section className="workspace">
      <SearchPanel
        examples={EXAMPLE_QUERIES}
        authorFilters={authorFilters}
        authorMatch={authorMatch}
        limit={limit}
        loading={loading}
        onLimitChange={setLimit}
        onAuthorFiltersChange={handleAuthorFiltersChange}
        onAuthorMatchChange={handleAuthorMatchChange}
        onQueryChange={handleQueryChange}
        onSubmit={submitSearch}
        query={query}
      />

      <ResultsPanel
        error={error}
        loading={loading}
        searchPayload={searchPayload}
        yearLabel={yearLabel}
      />
    </section>
  );

  return (
    <main className="app-shell">
      <Topbar health={health} />

      <OverviewStats stats={stats} repositories={repositories} />

      <Routes>
        <Route path="/" element={<Navigate to="/search" replace />} />
        <Route path="/search" element={searchPage} />
        <Route path="/admin" element={<AdminPanel onOverviewRefresh={loadOverview} />} />
        <Route path="/admin/login" element={<AdminPanel loginPage onOverviewRefresh={loadOverview} />} />
        <Route path="/admin/model-observability" element={<ModelObservabilityDashboard />} />
        <Route path="*" element={<Navigate to="/search" replace />} />
      </Routes>
    </main>
  );
}
