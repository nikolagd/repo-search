import { useCallback, useEffect, useMemo, useState } from "react";
import { BrainCircuit, Database, LogOut, RefreshCw, Shield, UserPlus } from "lucide-react";

import { fetchJson } from "../api/client";
import { formatDate } from "../utils/format";

export default function AdminPanel() {
  const [admin, setAdmin] = useState(null);
  const [sessionChecked, setSessionChecked] = useState(false);
  const [mode, setMode] = useState("login");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [repositories, setRepositories] = useState([]);
  const [embeddingStatus, setEmbeddingStatus] = useState(null);
  const [loading, setLoading] = useState(false);
  const [authError, setAuthError] = useState("");
  const [adminError, setAdminError] = useState("");

  const hasRunningHarvest = useMemo(
    () => repositories.some((repository) => repository.harvest_job?.status === "running"),
    [repositories],
  );
  const isEmbeddingRunning = embeddingStatus?.embedding_job?.status === "running";

  const clearSession = useCallback(() => {
    setAdmin(null);
    setRepositories([]);
    setEmbeddingStatus(null);
    setAdminError("");
  }, []);

  const loadAdminData = useCallback(async () => {
    const [repositoryData, embeddingData] = await Promise.all([
      fetchJson("/api/admin/repositories"),
      fetchJson("/api/admin/embeddings"),
    ]);
    setRepositories(repositoryData);
    setEmbeddingStatus(embeddingData);
  }, []);

  useEffect(() => {
    let ignore = false;

    async function restoreSession() {
      try {
        const payload = await fetchJson("/api/auth/me");

        if (!ignore) {
          setAdmin(payload);
          await loadAdminData();
        }
      } catch {
        if (!ignore) {
          clearSession();
        }
      } finally {
        if (!ignore) {
          setSessionChecked(true);
        }
      }
    }

    restoreSession();
    return () => {
      ignore = true;
    };
  }, [clearSession, loadAdminData]);

  useEffect(() => {
    if ((!hasRunningHarvest && !isEmbeddingRunning) || !admin) {
      return undefined;
    }

    const interval = window.setInterval(() => {
      loadAdminData().catch(() => undefined);
    }, 4000);

    return () => window.clearInterval(interval);
  }, [admin, hasRunningHarvest, isEmbeddingRunning, loadAdminData]);

  async function submitAuth(event) {
    event.preventDefault();
    setLoading(true);
    setAuthError("");
    setAdminError("");

    try {
      const payload = await fetchJson(`/api/auth/${mode}`, {
        method: "POST",
        body: JSON.stringify({ username, password }),
      });

      setAdmin(payload.admin);
      setPassword("");
      await loadAdminData();
    } catch (err) {
      setAuthError(err.message);
    } finally {
      setLoading(false);
    }
  }

  async function refreshRepository(repositoryId) {
    setAdminError("");

    try {
      await fetchJson(`/api/admin/repositories/${repositoryId}/harvest`, {
        method: "POST",
      });
      await loadAdminData();
    } catch (err) {
      setAdminError(err.message);
    }
  }

  async function embedMissingPublications() {
    setAdminError("");

    try {
      await fetchJson("/api/admin/embeddings/backfill", {
        method: "POST",
      });
      await loadAdminData();
    } catch (err) {
      setAdminError(err.message);
    }
  }

  async function logout() {
    setAuthError("");
    setAdminError("");

    try {
      await fetchJson("/api/auth/logout", {
        method: "POST",
      });
    } finally {
      clearSession();
    }
  }

  if (!sessionChecked) {
    return (
      <section className="admin-shell">
        <div className="empty-state">
          <RefreshCw aria-hidden="true" className="spin" size={24} />
          <span>Checking admin session...</span>
        </div>
      </section>
    );
  }

  if (!admin) {
    return (
      <section className="admin-shell">
        <form className="admin-auth-panel" onSubmit={submitAuth}>
          <div>
            <span className="eyebrow">Admin</span>
            <h2>{mode === "login" ? "Log in" : "Register"}</h2>
            {mode === "register" && (
              <p className="admin-help">Registration is only available while no admin account exists.</p>
            )}
          </div>

          <label htmlFor="admin-username">Username</label>
          <input
            id="admin-username"
            autoComplete="username"
            value={username}
            onChange={(event) => setUsername(event.target.value)}
          />

          <label htmlFor="admin-password">Password</label>
          <input
            id="admin-password"
            autoComplete={mode === "login" ? "current-password" : "new-password"}
            type="password"
            value={password}
            onChange={(event) => setPassword(event.target.value)}
          />

          {authError && <div className="admin-message error">{authError}</div>}

          <button className="primary-action" type="submit" disabled={loading || !username.trim() || password.length < 8}>
            {mode === "login" ? <Shield aria-hidden="true" size={18} /> : <UserPlus aria-hidden="true" size={18} />}
            {loading ? "Please wait..." : mode === "login" ? "Log in" : "Register"}
          </button>

          <button
            className="secondary-action"
            type="button"
            onClick={() => {
              setMode(mode === "login" ? "register" : "login");
              setAuthError("");
            }}
          >
            {mode === "login" ? "Initial admin setup" : "Use existing account"}
          </button>
        </form>
      </section>
    );
  }

  return (
    <section className="admin-shell">
      <div className="admin-header">
        <div>
          <span className="eyebrow">Admin</span>
          <h2>Repository harvest</h2>
        </div>
        <div className="admin-session">
          <span>{admin.username}</span>
          <button className="icon-action" type="button" onClick={logout} title="Log out">
            <LogOut aria-hidden="true" size={18} />
          </button>
        </div>
      </div>

      {adminError && <div className="admin-message error">{adminError}</div>}

      <article className="admin-tool-panel">
        <div className="admin-repository-main">
          <BrainCircuit aria-hidden="true" size={20} />
          <div>
            <h3>Missing embeddings</h3>
            <p>{embeddingStatus?.missing_embeddings ?? "-"} publications without embeddings</p>
            {embeddingStatus?.embedding_job && (
              <div className={`harvest-status ${embeddingStatus.embedding_job.status}`}>
                {embeddingStatus.embedding_job.message}
              </div>
            )}
          </div>
        </div>

        <button
          className="secondary-action refresh-action"
          type="button"
          onClick={embedMissingPublications}
          disabled={isEmbeddingRunning || !embeddingStatus?.missing_embeddings}
        >
          <RefreshCw aria-hidden="true" className={isEmbeddingRunning ? "spin" : ""} size={18} />
          {isEmbeddingRunning ? "Embedding" : "Embed missing"}
        </button>
      </article>

      <div className="admin-repository-list">
        {repositories.map((repository) => {
          const isRunning = repository.harvest_job?.status === "running";

          return (
            <article className="admin-repository" key={repository.id}>
              <div className="admin-repository-main">
                <Database aria-hidden="true" size={20} />
                <div>
                  <h3>{repository.name}</h3>
                  <p>{repository.oai_endpoint}</p>
                  <div className="result-meta">
                    <span>Last harvest: {formatDate(repository.last_harvest)}</span>
                    <span>Refresh interval: {repository.refresh_interval ?? "manual"}</span>
                  </div>
                  {repository.harvest_job && (
                    <div className={`harvest-status ${repository.harvest_job.status}`}>
                      {repository.harvest_job.message}
                    </div>
                  )}
                </div>
              </div>

              <button
                className="secondary-action refresh-action"
                type="button"
                onClick={() => refreshRepository(repository.id)}
                disabled={isRunning}
              >
                <RefreshCw aria-hidden="true" className={isRunning ? "spin" : ""} size={18} />
                {isRunning ? "Running" : "Refresh"}
              </button>
            </article>
          );
        })}
      </div>
    </section>
  );
}
