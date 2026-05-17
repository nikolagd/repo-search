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
  Users,
  X,
} from "lucide-react";
import { Link, Navigate, useNavigate } from "react-router-dom";

import { fetchJson, getErrorMessage } from "../api/client";
import type {
  AdminRepositoryResponse,
  AdminUser,
  AdminUserCreatePayload,
  AdminUserListResponse,
  AuthMode,
  AuthResponse,
  EmbeddingStatusResponse,
  HarvestJob,
  RepositoryResponse,
  RepositoryWritePayload,
  UserRole,
} from "../types";
import { formatDate } from "../utils/format";
import Button from "./ui/Button";
import TextField from "./ui/TextField";

const USER_ROLE_OPTIONS: UserRole[] = ["admin", "editor", "viewer"];

interface AdminPanelProps {
  authMode?: AuthMode;
  onOverviewRefresh: () => Promise<void>;
}

export default function AdminPanel({ authMode, onOverviewRefresh }: AdminPanelProps) {
  const navigate = useNavigate();
  const [admin, setAdmin] = useState<AdminUser | null>(null);
  const [sessionChecked, setSessionChecked] = useState(false);
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [repositories, setRepositories] = useState<AdminRepositoryResponse[]>([]);
  const [users, setUsers] = useState<AdminUserListResponse[]>([]);
  const [embeddingStatus, setEmbeddingStatus] = useState<EmbeddingStatusResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [adminRefreshing, setAdminRefreshing] = useState(false);
  const [authError, setAuthError] = useState("");
  const [adminError, setAdminError] = useState("");
  const [repositorySaving, setRepositorySaving] = useState(false);
  const [userSaving, setUserSaving] = useState(false);
  const [editingRepositoryId, setEditingRepositoryId] = useState<number | null>(null);
  const [repositoryForm, setRepositoryForm] = useState({
    name: "",
    oai_endpoint: "",
    refresh_interval: "",
  });
  const [userForm, setUserForm] = useState<AdminUserCreatePayload>({
    username: "",
    password: "",
    role: "viewer",
  });

  const hasRunningHarvest = useMemo(
    () => repositories.some((repository) => repository.harvest_job?.status === "running"),
    [repositories],
  );
  const isEmbeddingRunning = embeddingStatus?.embedding_job?.status === "running";
  const isEditingRepository = editingRepositoryId !== null;
  const canSaveRepository = Boolean(repositoryForm.name.trim() && repositoryForm.oai_endpoint.trim());
  const canManageData = admin?.role === "admin" || admin?.role === "editor";
  const canManageUsers = admin?.role === "admin";
  const canSaveUser = Boolean(userForm.username.trim() && userForm.password.length >= 8);

  const clearSession = useCallback(() => {
    setAdmin(null);
    setRepositories([]);
    setUsers([]);
    setEmbeddingStatus(null);
    setAdminError("");
    resetRepositoryForm();
    resetUserForm();
  }, []);

  function resetRepositoryForm() {
    setEditingRepositoryId(null);
    setRepositoryForm({
      name: "",
      oai_endpoint: "",
      refresh_interval: "",
    });
  }

  function resetUserForm() {
    setUserForm({
      username: "",
      password: "",
      role: "viewer",
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

  const loadUsers = useCallback(async () => {
    const payload = await fetchJson<AdminUserListResponse[]>("/api/admin/users");
    setUsers(payload);
  }, []);

  const refreshAdminData = useCallback(async () => {
    const tasks: Promise<unknown>[] = [
      loadAdminData(),
      onOverviewRefresh(),
    ];

    if (admin?.role === "admin") {
      tasks.push(loadUsers());
    } else {
      setUsers([]);
    }

    await Promise.all(tasks);
  }, [admin?.role, loadAdminData, loadUsers, onOverviewRefresh]);

  useEffect(() => {
    let ignore = false;

    async function restoreSession() {
      try {
        const payload = await fetchJson<AdminUser>("/api/auth/me");

        if (!ignore) {
          setAdmin(payload);
          await refreshAdminData();

          if (payload.role === "admin") {
            await loadUsers();
          }
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
  }, [clearSession, loadUsers, refreshAdminData]);

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
      const payload = await fetchJson<AuthResponse>(`/api/auth/${authMode}`, {
        method: "POST",
        body: JSON.stringify({ username, password }),
      });

      setAdmin(payload.admin);
      setPassword("");
      await refreshAdminData();

      if (payload.admin.role === "admin") {
        await loadUsers();
      }

      navigate("/admin", { replace: true });
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

  async function submitUserForm(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();

    if (!canManageUsers) {
      return;
    }

    setAdminError("");
    setUserSaving(true);

    try {
      await fetchJson<AdminUser>("/api/admin/users", {
        method: "POST",
        body: JSON.stringify({
          username: userForm.username.trim(),
          password: userForm.password,
          role: userForm.role,
        }),
      });
      resetUserForm();
      await loadUsers();
    } catch (err) {
      setAdminError(getErrorMessage(err, "User save failed"));
    } finally {
      setUserSaving(false);
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
    if (!authMode) {
      return <Navigate to="/admin/login" replace />;
    }

    return (
      <section className="admin-shell">
        <form className="admin-auth-panel" onSubmit={submitAuth}>
          <div>
            <span className="eyebrow">Admin</span>
            <h2>{authMode === "login" ? "Log in" : "Register"}</h2>
            {authMode === "register" && (
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
            autoComplete={authMode === "login" ? "current-password" : "new-password"}
            type="password"
            value={password}
            onChange={(event) => setPassword(event.target.value)}
          />

          {authError && <div className="admin-message error">{authError}</div>}

          <button className="primary-action" type="submit" disabled={loading || !username.trim() || password.length < 8}>
            {authMode === "login" ? <Shield aria-hidden="true" size={18} /> : <UserPlus aria-hidden="true" size={18} />}
            {loading ? "Please wait..." : authMode === "login" ? "Log in" : "Register"}
          </button>

          <Link className="secondary-action" to={authMode === "login" ? "/admin/register" : "/admin/login"}>
            {authMode === "login" ? "Initial admin setup" : "Use existing account"}
          </Link>
        </form>
      </section>
    );
  }

  if (authMode) {
    return <Navigate to="/admin" replace />;
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
          <span>{admin.username} · {admin.role}</span>
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
            disabled={!canManageData}
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
            disabled={!canManageData}
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
            disabled={!canManageData}
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
          disabled={!canManageData || repositorySaving || !canSaveRepository}
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

      {canManageUsers && (
        <article className="admin-tool-panel user-management-panel">
          <div className="admin-repository-main">
            <Users aria-hidden="true" size={20} />
            <div>
              <h3>User access</h3>
              <p>Create admin, editor, and viewer accounts for protected workflows.</p>
            </div>
          </div>

          <form className="user-form" onSubmit={submitUserForm}>
            <TextField
              autoComplete="username"
              id="new-user-username"
              label="Username"
              onValueChange={(value) => setUserForm((current) => ({ ...current, username: value }))}
              value={userForm.username}
            />
            <TextField
              autoComplete="new-password"
              id="new-user-password"
              label="Password"
              onValueChange={(value) => setUserForm((current) => ({ ...current, password: value }))}
              type="password"
              value={userForm.password}
            />
            <div className="field">
              <label htmlFor="new-user-role">Role</label>
              <select
                id="new-user-role"
                value={userForm.role}
                onChange={(event) => setUserForm((current) => ({
                  ...current,
                  role: event.target.value as UserRole,
                }))}
              >
                {USER_ROLE_OPTIONS.map((role) => (
                  <option key={role} value={role}>{role}</option>
                ))}
              </select>
            </div>
            <Button
              disabled={userSaving || !canSaveUser}
              icon={userSaving ? <RefreshCw aria-hidden="true" className="spin" size={18} /> : <UserPlus aria-hidden="true" size={18} />}
              type="submit"
              variant="primary"
            >
              {userSaving ? "Creating" : "Create user"}
            </Button>
          </form>

          <div className="user-list">
            {!!users.length && (
              <div className="user-list-header" aria-hidden="true">
                <span>User</span>
                <span>Role</span>
                <span>Created</span>
              </div>
            )}
            {users.map((user) => (
              <div className="user-list-row" key={user.id}>
                <span className="user-name">{user.username}</span>
                <strong className={`role-badge ${user.role}`}>{user.role}</strong>
                <span className="user-created">{formatDate(user.created_at)}</span>
              </div>
            ))}
          </div>
        </article>
      )}

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
          disabled={!canManageData || isEmbeddingRunning || !embeddingStatus?.missing_embeddings}
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
                  disabled={!canManageData || isRunning}
                >
                  <RefreshCw aria-hidden="true" className={isRunning ? "spin" : ""} size={18} />
                  {isRunning ? "Running" : "Refresh"}
                </button>
                <button
                  className="icon-action"
                  type="button"
                  onClick={() => startEditingRepository(repository)}
                  disabled={!canManageData || isRunning || repositorySaving}
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
