import type { LucideIcon } from "lucide-react";
import type { ReactNode } from "react";

interface StatProps {
  icon: LucideIcon;
  label: string;
  value: ReactNode;
}

export default function Stat({ icon: Icon, label, value }: StatProps) {
  return (
    <section className="stat">
      <Icon aria-hidden="true" size={18} />
      <div>
        <span>{label}</span>
        <strong>{value}</strong>
      </div>
    </section>
  );
}
