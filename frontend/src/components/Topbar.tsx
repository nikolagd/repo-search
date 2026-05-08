import type { MouseEvent } from "react";
import { Search, Server, Shield } from "lucide-react";
import type { HealthResponse, ViewMode } from "../types";

interface TopbarProps {
  activeView: ViewMode;
  health: HealthResponse | null;
  onViewChange: (view: ViewMode) => void;
}

export default function Topbar({ activeView, health, onViewChange }: TopbarProps) {
  function handleNavigation(event: MouseEvent<HTMLAnchorElement>, view: ViewMode) {
    if (
      event.button !== 0 ||
      event.metaKey ||
      event.ctrlKey ||
      event.altKey ||
      event.shiftKey
    ) {
      return;
    }

    event.preventDefault();
    onViewChange(view);
  }

  return (
    <header className="topbar">
      <div>
        <span className="eyebrow">Repository intelligence</span>
        <h1>Repo Search</h1>
      </div>
      <div className="topbar-actions">
        <div className="view-switch" aria-label="Primary navigation">
          <a
            className={activeView === "search" ? "active" : ""}
            href="/search"
            onClick={(event) => handleNavigation(event, "search")}
          >
            <Search aria-hidden="true" size={16} />
            Search
          </a>
          <a
            className={activeView === "admin" ? "active" : ""}
            href="/admin"
            onClick={(event) => handleNavigation(event, "admin")}
          >
            <Shield aria-hidden="true" size={16} />
            Admin
          </a>
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
