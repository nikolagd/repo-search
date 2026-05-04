import { Search, Server, Shield } from "lucide-react";

export default function Topbar({ activeView, health, onViewChange }) {
  return (
    <header className="topbar">
      <div>
        <span className="eyebrow">Repository intelligence</span>
        <h1>Repo Search</h1>
      </div>
      <div className="topbar-actions">
        <div className="view-switch" aria-label="Primary navigation">
          <button
            className={activeView === "search" ? "active" : ""}
            type="button"
            onClick={() => onViewChange("search")}
          >
            <Search aria-hidden="true" size={16} />
            Search
          </button>
          <button
            className={activeView === "admin" ? "active" : ""}
            type="button"
            onClick={() => onViewChange("admin")}
          >
            <Shield aria-hidden="true" size={16} />
            Admin
          </button>
        </div>
        <div className="system-status" title="API and database status">
          <Server aria-hidden="true" size={18} />
          <span>API {health?.status || "..."}</span>
          <span className={health?.database === "ok" ? "dot ok" : "dot"} />
          <span>DB {health?.database || "..."}</span>
        </div>
      </div>
    </header>
  );
}
