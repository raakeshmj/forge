// Geometry for the control plane's small SVG charts. Pure, so charts render identically on
// the server and the client and can be unit-tested without a DOM.

/** Clamp a ratio into [0, 1]; non-finite values (NaN, ±Infinity) count as 0. */
export function unit(ratio: number): number {
  return Number.isFinite(ratio) ? Math.max(0, Math.min(1, ratio)) : 0;
}

export interface Ring {
  radius: number;
  circumference: number;
  /** stroke-dasharray for the filled arc: filled length, then the rest of the circle. */
  dash: string;
}

/** A ring gauge of `size` px with a stroke of `stroke` px, filled to `ratio`. */
export function ring(ratio: number, size: number, stroke: number): Ring {
  const radius = (size - stroke) / 2;
  const circumference = 2 * Math.PI * radius;
  const filled = unit(ratio) * circumference;
  return { radius, circumference, dash: `${filled.toFixed(2)} ${(circumference - filled).toFixed(2)}` };
}

export interface Column {
  key: string;
  value: number;
  x: number;
  y: number;
  width: number;
  height: number;
}

/**
 * Columns for a bar chart of `width` x `height` px, scaled to the largest value.
 * A non-zero value always gets at least 2 px so it stays visible next to a large one;
 * bars are at most `maxBar` px wide, centred in their slot, so sparse data stays legible.
 */
export function columns(
  rows: readonly { key: string; value: number }[],
  width: number,
  height: number,
  gap = 4,
  maxBar = 56,
): Column[] {
  if (!rows.length) return [];
  const top = Math.max(0, ...rows.map((row) => row.value));
  const slot = width / rows.length;
  const barWidth = Math.max(1, Math.min(maxBar, slot - gap));
  return rows.map((row, index) => {
    const scaled = top > 0 ? (Math.max(0, row.value) / top) * height : 0;
    const barHeight = row.value > 0 ? Math.max(2, scaled) : 0;
    return {
      key: row.key,
      value: row.value,
      x: index * slot + (slot - barWidth) / 2,
      y: height - barHeight,
      width: barWidth,
      height: barHeight,
    };
  });
}

export interface Segment {
  key: string;
  count: number;
  share: number;
}

/** Shares of a whole, largest first, dropping empty keys; for stacked distribution bars. */
export function segments(rows: readonly { key: string; count: number }[]): Segment[] {
  const total = rows.reduce((sum, row) => sum + Math.max(0, row.count), 0);
  if (total === 0) return [];
  return rows
    .filter((row) => row.count > 0)
    .map((row) => ({ key: row.key, count: row.count, share: row.count / total }))
    .sort((a, b) => b.count - a.count || a.key.localeCompare(b.key));
}
