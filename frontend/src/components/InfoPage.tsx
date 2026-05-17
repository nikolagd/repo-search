import { Database, KeyRound, Route, ShieldCheck } from "lucide-react";
import Card from "./ui/Card";

const sections = [
  {
    icon: Route,
    title: "Site routes",
    text: "Search, admin access, login/register, and this overview are reachable through React Router.",
  },
  {
    icon: Database,
    title: "Repository data",
    text: "The application reads repositories, publications, authors, harvest jobs, and embeddings from PostgreSQL.",
  },
  {
    icon: ShieldCheck,
    title: "Protected actions",
    text: "Repository changes, harvest jobs, embedding backfill, and user management require an authenticated role.",
  },
  {
    icon: KeyRound,
    title: "User roles",
    text: "Admin users can manage accounts; editors can manage repository data; viewers can inspect protected data.",
  },
];

export default function InfoPage() {
  return (
    <section className="info-page">
      <div className="info-header">
        <span className="eyebrow">Overview</span>
        <h2>Repository search platform</h2>
        <p>
          A React and FastAPI application for semantic search over academic repository records,
          with protected administration tools for data refresh and embedding maintenance.
        </p>
      </div>

      <div className="info-grid">
        {sections.map(({ icon: Icon, title, text }) => (
          <Card className="info-card" key={title}>
            <Icon aria-hidden="true" size={22} />
            <h3>{title}</h3>
            <p>{text}</p>
          </Card>
        ))}
      </div>
    </section>
  );
}
