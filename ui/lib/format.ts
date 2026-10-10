// Presentation helpers. Pure, so they render identically on server and client.

export function usd(microUsd: number): string {
  const dollars = microUsd / 1_000_000;
  if (dollars === 0) return "$0";
  if (Math.abs(dollars) < 0.01) return `$${dollars.toFixed(6)}`;
  return `$${dollars.toFixed(dollars < 100 ? 4 : 2)}`;
}

export function num(value: number): string {
  return new Intl.NumberFormat("en-US").format(value);
}

export function pct(ratio: number): string {
  return `${(ratio * 100).toFixed(1)}%`;
}

export function ms(value: number | null | undefined): string {
  if (value === null || value === undefined) return "—";
  return value < 1000 ? `${Math.round(value)} ms` : `${(value / 1000).toFixed(2)} s`;
}

export function when(iso: string | null | undefined): string {
  if (!iso) return "—";
  return new Date(iso).toISOString().replace("T", " ").slice(0, 19) + "Z";
}

export function short(id: string): string {
  return id.slice(0, 8);
}

export function score(value: number | null | undefined): string {
  return value === null || value === undefined ? "n/a" : value.toFixed(2);
}

export const TERMINAL = new Set(["COMPLETED", "FAILED", "CANCELLED"]);
