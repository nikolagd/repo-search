import type { HTMLAttributes } from "react";

export default function Card({ children, className = "", ...props }: HTMLAttributes<HTMLElement>) {
  return (
    <article className={`card ${className}`.trim()} {...props}>
      {children}
    </article>
  );
}
