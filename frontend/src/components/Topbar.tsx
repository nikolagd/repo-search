import { Info, Search, Server, Shield } from "lucide-react";
import { NavLink } from "react-router-dom";
import type { HealthResponse } from "../types";

interface TopbarProps {
  health: HealthResponse | null;
}

export default function Topbar({ health }: TopbarProps) {
  return (
    <header className="topbar">
      <div>
        <h1>Repo Search</h1>
      </div>
      <div className="topbar-actions">
        <div className="view-switch" aria-label="Primary navigation">
          <NavLink
            className={({ isActive }) => (isActive ? "active" : "")}
            to="/search"
          >
            <Search aria-hidden="true" size={16} />
            Search
          </NavLink>
          <NavLink
            className={({ isActive }) => (isActive ? "active" : "")}
            to="/admin"
          >
            <Shield aria-hidden="true" size={16} />
            Admin
          </NavLink>
          <NavLink
            className={({ isActive }) => (isActive ? "active" : "")}
            to="/about"
          >
            <Info aria-hidden="true" size={16} />
            About
          </NavLink>
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
