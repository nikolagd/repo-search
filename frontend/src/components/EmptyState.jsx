import { AlertCircle, RefreshCw, Search } from "lucide-react";

export default function EmptyState({ loading, error }) {
  if (loading) {
    return (
      <div className="empty-state">
        <RefreshCw aria-hidden="true" className="spin" size={24} />
        <span>Searching publications...</span>
      </div>
    );
  }

  if (error) {
    return (
      <div className="empty-state error">
        <AlertCircle aria-hidden="true" size={24} />
        <span>{error}</span>
      </div>
    );
  }

  return (
    <div className="empty-state">
      <Search aria-hidden="true" size={24} />
      <span>Search results will appear here.</span>
    </div>
  );
}
