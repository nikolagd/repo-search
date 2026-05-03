import { Activity, Database, RefreshCw, Search } from "lucide-react";

import { formatDate } from "../utils/format";
import Stat from "./Stat";

export default function OverviewStats({ stats, repositories }) {
  return (
    <section className="overview">
      <Stat icon={Database} label="Repositories" value={stats?.repositories ?? repositories.length ?? "-"} />
      <Stat icon={Activity} label="Publications" value={stats?.publications ?? "-"} />
      <Stat icon={Search} label="Embedded" value={stats?.publications_with_embeddings ?? "-"} />
      <Stat icon={RefreshCw} label="Last publication" value={formatDate(stats?.last_harvest)} />
    </section>
  );
}
