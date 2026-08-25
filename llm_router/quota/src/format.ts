/** Number and time formatting shared by the terminal table and the HTML page. */

/** 512400 -> "512.4K". Token budgets are quoted in thousands everywhere else. */
export function compact(value: number): string {
  if (value >= 1_000_000) return `${(value / 1_000_000).toFixed(1)}M`;
  if (value >= 1_000) return `${(value / 1_000).toFixed(1)}K`;
  return String(Math.round(value));
}

export function percent(ratio: number): string {
  if (ratio > 0 && ratio < 0.01) return "<1%";
  return `${Math.round(ratio * 100)}%`;
}

export function iso(seconds: number): string {
  return new Date(seconds * 1000).toISOString().replace("T", " ").slice(0, 19) + "Z";
}

export function ago(seconds: number, now: number): string {
  const delta = Math.max(0, now - seconds);
  if (delta < 90) return `${Math.round(delta)}s ago`;
  if (delta < 5400) return `${Math.round(delta / 60)}m ago`;
  if (delta < 172800) return `${Math.round(delta / 3600)}h ago`;
  return `${Math.round(delta / 86400)}d ago`;
}
