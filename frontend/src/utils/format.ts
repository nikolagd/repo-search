export function formatDate(value: string | null | undefined): string {
  if (!value) {
    return "No date";
  }

  return new Intl.DateTimeFormat("en", {
    year: "numeric",
    month: "short",
    day: "2-digit",
  }).format(new Date(value));
}

export function formatScore(value: number | null | undefined): string {
  return Number(value || 0).toFixed(3);
}
