import { Server } from "lucide-react";

export default function Topbar({ health }) {
  return (
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
  );
}
