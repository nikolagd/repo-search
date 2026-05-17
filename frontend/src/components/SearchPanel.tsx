import type { FormEvent } from "react";
import { RefreshCw, Search } from "lucide-react";
import Button from "./ui/Button";
import TextField from "./ui/TextField";

interface SearchPanelProps {
  examples: string[];
  limit: number;
  loading: boolean;
  onLimitChange: (limit: number) => void;
  onQueryChange: (query: string) => void;
  onSubmit: (event: FormEvent<HTMLFormElement>) => void;
  query: string;
}

export default function SearchPanel({
  examples,
  limit,
  loading,
  onLimitChange,
  onQueryChange,
  onSubmit,
  query,
}: SearchPanelProps) {
  return (
    <form className="search-panel" onSubmit={onSubmit}>
      <TextField
        id="query"
        label="Search query"
        multiline
        onValueChange={onQueryChange}
        rows={4}
        value={query}
      />

      <div className="examples" aria-label="Example queries">
        {examples.map((example) => (
          <Button
            key={example}
            onClick={() => onQueryChange(example)}
          >
            {example}
          </Button>
        ))}
      </div>

      <div className="controls">
        <TextField
          id="limit"
          label="Results"
          min={1}
          max={50}
          onValueChange={(nextLimit) => onLimitChange(Number(nextLimit))}
          type="number"
          value={limit}
        />
        <Button
          disabled={loading || !query.trim()}
          icon={loading ? <RefreshCw aria-hidden="true" className="spin" size={18} /> : <Search aria-hidden="true" size={18} />}
          type="submit"
          variant="primary"
        >
          Search
        </Button>
      </div>
    </form>
  );
}
