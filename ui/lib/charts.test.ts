import { describe, expect, it } from "vitest";

import { columns, ring, segments, unit } from "./charts";

describe("unit", () => {
  it("clamps ratios and treats non-finite values as zero", () => {
    expect(unit(0.4)).toBe(0.4);
    expect(unit(-1)).toBe(0);
    expect(unit(3)).toBe(1);
    expect(unit(Number.NaN)).toBe(0);
    expect(unit(Number.POSITIVE_INFINITY)).toBe(0);
  });
});

describe("ring", () => {
  it("fills the arc in proportion to the ratio", () => {
    const half = ring(0.5, 100, 10);
    expect(half.radius).toBe(45);
    const [filled, rest] = half.dash.split(" ").map(Number);
    expect(filled).toBeCloseTo(half.circumference / 2, 1);
    expect((filled ?? 0) + (rest ?? 0)).toBeCloseTo(half.circumference, 1);
  });

  it("never overfills or underfills", () => {
    expect(ring(2, 100, 10).dash.startsWith(ring(1, 100, 10).dash.split(" ")[0] ?? "")).toBe(true);
    expect(ring(-1, 100, 10).dash.split(" ")[0]).toBe("0.00");
  });
});

describe("columns", () => {
  it("scales to the largest value and keeps small values visible", () => {
    const bars = columns(
      [
        { key: "a", value: 1000 },
        { key: "b", value: 1 },
        { key: "c", value: 0 },
      ],
      300,
      100,
    );
    expect(bars.map((bar) => bar.height)).toEqual([100, 2, 0]);
    expect(bars.every((bar) => bar.y + bar.height === 100)).toBe(true);
    expect(bars[1]?.x).toBeGreaterThan(bars[0]?.x ?? 0);
  });

  it("caps bar width and centres a lone bar", () => {
    const [bar] = columns([{ key: "a", value: 5 }], 600, 100, 6, 56);
    expect(bar?.width).toBe(56);
    expect((bar?.x ?? 0) + (bar?.width ?? 0) / 2).toBe(300);
  });

  it("handles empty and all-zero data", () => {
    expect(columns([], 100, 50)).toEqual([]);
    expect(columns([{ key: "a", value: 0 }], 100, 50)[0]?.height).toBe(0);
  });
});

describe("segments", () => {
  it("returns shares of the whole, largest first, without empty keys", () => {
    const parts = segments([
      { key: "FAILED", count: 1 },
      { key: "COMPLETED", count: 3 },
      { key: "RUNNING", count: 0 },
    ]);
    expect(parts.map((part) => part.key)).toEqual(["COMPLETED", "FAILED"]);
    expect(parts[0]?.share).toBe(0.75);
  });

  it("is empty when nothing has happened", () => {
    expect(segments([{ key: "COMPLETED", count: 0 }])).toEqual([]);
  });
});
