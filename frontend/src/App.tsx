import { type FormEvent, useCallback, useEffect, useMemo, useState } from "react";
import { Navigate, Route, Routes } from "react-router-dom";

import { fetchJson, getErrorMessage } from "./api/client";
import AdminPanel from "./components/AdminPanel";
import OverviewStats from "./components/OverviewStats";
import ResultsPanel from "./components/ResultsPanel";
import SearchPanel from "./components/SearchPanel";
import Topbar from "./components/Topbar";
import { EXAMPLE_QUERIES } from "./constants/searchExamples";
import type { HealthResponse, RepositoryResponse, SearchResponse, StatsResponse } from "./types";

export default function App() {
  const [query, setQuery] = useState(EXAMPLE_QUERIES[0]);
  const [limit, setLimit] = useState(10);
  const [health, setHealth] = useState<HealthResponse | null>(null);
  const [stats, setStats] = useState<StatsResponse | null>(null);
  const [repositories, setRepositories] = useState<RepositoryResponse[]>([]);
  const [searchPayload, setSearchPayload] = useState<SearchResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

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

    setLoading(true);
    setError("");

    try {
      const payload = await fetchJson<SearchResponse>("/api/search", {
        method: "POST",
        body: JSON.stringify({ query, limit }),
      });
      setSearchPayload(payload);
    } catch (err) {
      setError(getErrorMessage(err, "Search failed"));
      setSearchPayload(null);
    } finally {
      setLoading(false);
    }
  }

  const searchPage = (
    <section className="workspace">
      <SearchPanel
        examples={EXAMPLE_QUERIES}
        limit={limit}
        loading={loading}
        onLimitChange={setLimit}
        onQueryChange={setQuery}
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
        <Route path="*" element={<Navigate to="/search" replace />} />
      </Routes>
    </main>
  );
}
