import { describe, expect, it } from "vitest";

import { COLUMN, ROW, layout } from "./graph";

describe("layout", () => {
  it("places each node one column right of its deepest dependency (fan-out, fan-in)", () => {
    const placed = layout(
      [{ id: "plan", label: "plan" }, { id: "a", label: "a" }, { id: "b", label: "b" }, { id: "join", label: "join" }],
      [
        { from: "plan", to: "a" },
        { from: "plan", to: "b" },
        { from: "a", to: "join" },
        { from: "b", to: "join" },
        { from: "plan", to: "join" },
      ],
    );
    const at = Object.fromEntries(placed.map((p) => [p.id, [p.x, p.y]]));
    expect(at).toEqual({ plan: [0, 0], a: [COLUMN, 0], b: [COLUMN, ROW], join: [2 * COLUMN, 0] });
  });

  it("ignores edges to unknown nodes and survives cycles in bad data", () => {
    const placed = layout(
      [{ id: "x", label: "x" }, { id: "y", label: "y" }],
      [
        { from: "x", to: "y" },
        { from: "y", to: "x" },
        { from: "ghost", to: "x" },
      ],
    );
    expect(placed).toHaveLength(2);
    expect(new Set(placed.map((p) => `${p.x},${p.y}`)).size).toBe(2); // no overlap
  });

  it("is deterministic", () => {
    const nodes = [{ id: "a", label: "a" }, { id: "b", label: "b" }];
    expect(layout(nodes, [])).toEqual(layout(nodes, []));
  });
});
