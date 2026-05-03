import { useEffect, useMemo, useState } from "react";

import { fetchJson } from "./api/client";
import OverviewStats from "./components/OverviewStats";
import ResultsPanel from "./components/ResultsPanel";
import SearchPanel from "./components/SearchPanel";
import Topbar from "./components/Topbar";
import { EXAMPLE_QUERIES } from "./constants/searchExamples";

export default function App() {
  const [query, setQuery] = useState(EXAMPLE_QUERIES[0]);
  const [limit, setLimit] = useState(10);
  const [health, setHealth] = useState(null);
  const [stats, setStats] = useState(null);
  const [repositories, setRepositories] = useState([]);
  const [searchPayload, setSearchPayload] = useState(null);
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
      <Topbar health={health} />

      <OverviewStats stats={stats} repositories={repositories} />

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
    </main>
  );
}
