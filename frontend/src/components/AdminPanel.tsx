import { type FormEvent, useCallback, useEffect, useMemo, useState } from "react";
import {
  BrainCircuit,
  Database,
  Edit3,
  LogOut,
  Plus,
  RefreshCw,
  Save,
  Shield,
  UserPlus,
  X,
} from "lucide-react";

import { fetchJson, getErrorMessage } from "../api/client";
import type {
  AdminRepositoryResponse,
  AdminUser,
  AuthMode,
  AuthResponse,
  EmbeddingStatusResponse,
  HarvestJob,
  RepositoryResponse,
  RepositoryWritePayload,
} from "../types";
import { formatDate } from "../utils/format";

interface AdminPanelProps {
  onOverviewRefresh: () => Promise<void>;
}

export default function AdminPanel({ onOverviewRefresh }: AdminPanelProps) {
  const [admin, setAdmin] = useState<AdminUser | null>(null);
  const [sessionChecked, setSessionChecked] = useState(false);
  const [mode, setMode] = useState<AuthMode>("login");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [repositories, setRepositories] = useState<AdminRepositoryResponse[]>([]);
  const [embeddingStatus, setEmbeddingStatus] = useState<EmbeddingStatusResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [adminRefreshing, setAdminRefreshing] = useState(false);
  const [authError, setAuthError] = useState("");
  const [adminError, setAdminError] = useState("");
  const [repositorySaving, setRepositorySaving] = useState(false);
  const [editingRepositoryId, setEditingRepositoryId] = useState<number | null>(null);
  const [repositoryForm, setRepositoryForm] = useState({
    name: "",
    oai_endpoint: "",
    refresh_interval: "",
  });

  const hasRunningHarvest = useMemo(
    () => repositories.some((repository) => repository.harvest_job?.status === "running"),
    [repositories],
  );
  const isEmbeddingRunning = embeddingStatus?.embedding_job?.status === "running";
  const isEditingRepository = editingRepositoryId !== null;
  const canSaveRepository = Boolean(repositoryForm.name.trim() && repositoryForm.oai_endpoint.trim());

  const clearSession = useCallback(() => {
    setAdmin(null);
    setRepositories([]);
    setEmbeddingStatus(null);
    setAdminError("");
    resetRepositoryForm();
  }, []);

  function resetRepositoryForm() {
    setEditingRepositoryId(null);
    setRepositoryForm({
      name: "",
      oai_endpoint: "",
      refresh_interval: "",
    });
  }

  const loadAdminData = useCallback(async () => {
    const [repositoryData, embeddingData] = await Promise.all([
      fetchJson<AdminRepositoryResponse[]>("/api/admin/repositories"),
      fetchJson<EmbeddingStatusResponse>("/api/admin/embeddings"),
    ]);
    setRepositories(repositoryData);
    setEmbeddingStatus(embeddingData);
  }, []);

  const refreshAdminData = useCallback(async () => {
    await Promise.all([
      loadAdminData(),
      onOverviewRefresh(),
    ]);
  }, [loadAdminData, onOverviewRefresh]);

  useEffect(() => {
    let ignore = false;

    async function restoreSession() {
      try {
        const payload = await fetchJson<AdminUser>("/api/auth/me");

        if (!ignore) {
          setAdmin(payload);
          await refreshAdminData();
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
  }, [clearSession, refreshAdminData]);

  useEffect(() => {
    if ((!hasRunningHarvest && !isEmbeddingRunning) || !admin) {
      return undefined;
    }

    const interval = window.setInterval(() => {
      refreshAdminData().catch(() => undefined);
    }, 4000);

    return () => window.clearInterval(interval);
  }, [admin, hasRunningHarvest, isEmbeddingRunning, refreshAdminData]);

  async function manuallyRefreshAdminData() {
    setAdminError("");
    setAdminRefreshing(true);

    try {
      await refreshAdminData();
    } catch (err) {
      setAdminError(getErrorMessage(err, "Admin refresh failed"));
    } finally {
      setAdminRefreshing(false);
    }
  }

  async function submitAuth(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setLoading(true);
    setAuthError("");
    setAdminError("");

    try {
      const payload = await fetchJson<AuthResponse>(`/api/auth/${mode}`, {
        method: "POST",
        body: JSON.stringify({ username, password }),
      });

      setAdmin(payload.admin);
      setPassword("");
      await refreshAdminData();
    } catch (err) {
      setAuthError(getErrorMessage(err, "Authentication failed"));
    } finally {
      setLoading(false);
    }
  }

  async function refreshRepository(repositoryId: number) {
    setAdminError("");

    try {
      await fetchJson(`/api/admin/repositories/${repositoryId}/harvest`, {
        method: "POST",
      });
      await refreshAdminData();
    } catch (err) {
      setAdminError(getErrorMessage(err, "Harvest failed"));
    }
  }

  function buildRepositoryPayload(): RepositoryWritePayload {
    const refreshInterval = repositoryForm.refresh_interval.trim();

    return {
      name: repositoryForm.name.trim(),
      oai_endpoint: repositoryForm.oai_endpoint.trim(),
      refresh_interval: refreshInterval ? Number(refreshInterval) : null,
    };
  }

  function startEditingRepository(repository: AdminRepositoryResponse) {
    setAdminError("");
    setEditingRepositoryId(repository.id);
    setRepositoryForm({
      name: repository.name,
      oai_endpoint: repository.oai_endpoint,
      refresh_interval: repository.refresh_interval?.toString() ?? "",
    });
  }

  async function submitRepositoryForm(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setAdminError("");
    setRepositorySaving(true);

    try {
      const payload = buildRepositoryPayload();

      if (isEditingRepository) {
        await fetchJson<RepositoryResponse>(`/api/admin/repositories/${editingRepositoryId}`, {
          method: "PUT",
          body: JSON.stringify(payload),
        });
      } else {
        await fetchJson<RepositoryResponse>("/api/admin/repositories", {
          method: "POST",
          body: JSON.stringify(payload),
        });
      }

      resetRepositoryForm();
      await refreshAdminData();
    } catch (err) {
      setAdminError(getErrorMessage(err, "Repository save failed"));
    } finally {
      setRepositorySaving(false);
    }
  }

  async function embedMissingPublications() {
    setAdminError("");

    try {
      await fetchJson("/api/admin/embeddings/backfill", {
        method: "POST",
      });
      await refreshAdminData();
    } catch (err) {
      setAdminError(getErrorMessage(err, "Embedding backfill failed"));
    }
  }

  async function acknowledgeJob(job: HarvestJob) {
    if (!job.id || job.status === "running") {
      return;
    }

    setAdminError("");

    try {
      await fetchJson<{ status: string }>(`/api/admin/jobs/${job.id}/acknowledge`, {
        method: "POST",
      });
      await refreshAdminData();
    } catch (err) {
      setAdminError(getErrorMessage(err, "Could not dismiss job status"));
    }
  }

  function renderJobStatus(job: HarvestJob) {
    return (
      <div className={`harvest-status ${job.status}`}>
        <span>{job.message}</span>
        {job.status !== "running" && job.id && (
          <button
            className="status-dismiss"
            type="button"
            onClick={() => acknowledgeJob(job)}
            title="Dismiss status"
          >
            <X aria-hidden="true" size={13} />
          </button>
        )}
      </div>
    );
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
          <button
            className="secondary-action refresh-action"
            type="button"
            onClick={manuallyRefreshAdminData}
            disabled={adminRefreshing}
          >
            <RefreshCw aria-hidden="true" className={adminRefreshing ? "spin" : ""} size={18} />
            Refresh
          </button>
          <span>{admin.username}</span>
          <button className="icon-action" type="button" onClick={logout} title="Log out">
            <LogOut aria-hidden="true" size={18} />
          </button>
        </div>
      </div>

      {adminError && <div className="admin-message error">{adminError}</div>}

      <form className="repository-form" onSubmit={submitRepositoryForm}>
        <div className="repository-form-header">
          <div>
            <span className="eyebrow">Repositories</span>
            <h3>{isEditingRepository ? "Edit repository" : "Add repository"}</h3>
          </div>
          {isEditingRepository && (
            <button className="icon-action" type="button" onClick={resetRepositoryForm} title="Cancel edit">
              <X aria-hidden="true" size={18} />
            </button>
          )}
        </div>

        <div className="repository-form-grid">
          <label htmlFor="repository-name">Name</label>
          <input
            id="repository-name"
            value={repositoryForm.name}
            onChange={(event) => setRepositoryForm((current) => ({
              ...current,
              name: event.target.value,
            }))}
            placeholder="Faculty repository"
          />

          <label htmlFor="repository-endpoint">OAI endpoint</label>
          <input
            id="repository-endpoint"
            value={repositoryForm.oai_endpoint}
            onChange={(event) => setRepositoryForm((current) => ({
              ...current,
              oai_endpoint: event.target.value,
            }))}
            placeholder="https://example.edu/oai/request"
          />

          <label htmlFor="repository-refresh-interval">Refresh interval</label>
          <input
            id="repository-refresh-interval"
            type="number"
            min="1"
            value={repositoryForm.refresh_interval}
            onChange={(event) => setRepositoryForm((current) => ({
              ...current,
              refresh_interval: event.target.value,
            }))}
            placeholder="Manual"
          />
        </div>

        <button
          className="primary-action repository-save-action"
          type="submit"
          disabled={repositorySaving || !canSaveRepository}
        >
          {repositorySaving ? (
            <RefreshCw aria-hidden="true" className="spin" size={18} />
          ) : isEditingRepository ? (
            <Save aria-hidden="true" size={18} />
          ) : (
            <Plus aria-hidden="true" size={18} />
          )}
          {repositorySaving ? "Saving" : isEditingRepository ? "Save changes" : "Add repository"}
        </button>
      </form>

      <article className="admin-tool-panel">
        <div className="admin-repository-main">
          <BrainCircuit aria-hidden="true" size={20} />
          <div>
            <h3>Missing embeddings</h3>
            <p>{embeddingStatus?.missing_embeddings ?? "-"} publications without embeddings</p>
            {embeddingStatus?.embedding_job && renderJobStatus(embeddingStatus.embedding_job)}
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
                  {repository.harvest_job && renderJobStatus(repository.harvest_job)}
                </div>
              </div>

              <div className="admin-repository-actions">
                <button
                  className="secondary-action refresh-action"
                  type="button"
                  onClick={() => refreshRepository(repository.id)}
                  disabled={isRunning}
                >
                  <RefreshCw aria-hidden="true" className={isRunning ? "spin" : ""} size={18} />
                  {isRunning ? "Running" : "Refresh"}
                </button>
                <button
                  className="icon-action"
                  type="button"
                  onClick={() => startEditingRepository(repository)}
                  disabled={isRunning || repositorySaving}
                  title="Edit repository"
                >
                  <Edit3 aria-hidden="true" size={18} />
                </button>
              </div>
            </article>
          );
        })}
      </div>
    </section>
  );
}
