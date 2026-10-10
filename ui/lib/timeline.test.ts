import { describe, expect, it } from "vitest";

import { timeline } from "./timeline";
import type { EventView } from "./types";

let seq = 0;
function event(event_type: string, payload: Record<string, unknown>, at: string, aggregate = "x1"): EventView {
  seq += 1;
  return {
    id: `e${seq}`,
    event_type,
    aggregate_type: "execution",
    aggregate_id: aggregate,
    sequence: seq,
    payload,
    correlation_id: null,
    causation_id: null,
    occurred_at: at,
  };
}

const usage = (input: number, read: number, write: number) => ({
  input_tokens: input,
  output_tokens: 10,
  cache_read_input_tokens: read,
  cache_creation_input_tokens: write,
});

describe("timeline", () => {
  it("pairs tool calls with their outcomes and measures their latency", () => {
    const view = timeline([
      event("tool.called", { tool_use_id: "c1", tool: "metrics.read", invocation: 1 }, "2026-10-10T10:00:00.000Z"),
      event("tool.completed", { tool_use_id: "c1", tool: "metrics.read", is_error: false, attempts: 1 }, "2026-10-10T10:00:00.250Z"),
      event("tool.called", { tool_use_id: "c2", tool: "deploy", invocation: 2 }, "2026-10-10T10:00:01.000Z"),
      event("tool.failed", { tool_use_id: "c2", tool: "deploy", error_type: "TimeoutError", attempts: 3 }, "2026-10-10T10:00:04.000Z"),
      event("tool.denied", { tool_use_id: "c3", tool: "db.drop", stage: "policy", reason: "no" }, "2026-10-10T10:00:05.000Z"),
    ]);

    expect(view.toolCalls.map((c) => [c.tool, c.outcome, c.latencyMs, c.invocation])).toEqual([
      ["metrics.read", "completed", 250, 1],
      ["deploy", "failed", 3000, 2],
      ["db.drop", "denied", null, 0],
    ]);
    expect(view.toolCalls[2]?.detail).toBe("policy: no");
  });

  it("keeps tool calls of different executions apart even with the same tool_use_id", () => {
    const view = timeline([
      event("tool.called", { tool_use_id: "c1", tool: "a" }, "2026-10-10T10:00:00Z", "x1"),
      event("tool.called", { tool_use_id: "c1", tool: "a" }, "2026-10-10T10:00:00Z", "x2"),
      event("tool.completed", { tool_use_id: "c1", is_error: true }, "2026-10-10T10:00:01Z", "x2"),
    ]);
    expect(view.toolCalls.map((c) => c.outcome)).toEqual(["pending", "error"]);
  });

  it("totals model usage and computes the cache hit rate over all prompt tokens", () => {
    const view = timeline([
      event("model.called", { provider: "demo", model: "m", turn: 0, latency_ms: 100, usage: usage(100, 0, 300), cost_micro_usd: 50, cache_savings_micro_usd: 0 }, "2026-10-10T10:00:00Z"),
      event("model.called", { provider: "demo", model: "m", turn: 1, latency_ms: 40, usage: usage(100, 300, 0), cost_micro_usd: 20, cache_savings_micro_usd: 30 }, "2026-10-10T10:00:02Z"),
    ]);

    expect(view.modelCalls.map((c) => c.model)).toEqual(["demo/m", "demo/m"]);
    expect(view.totals).toMatchObject({ latencyMs: 140, costMicroUsd: 70, savingsMicroUsd: 30, cacheReadTokens: 300 });
    expect(view.totals.cacheHitRate).toBeCloseTo(300 / 800);
  });

  it("orders by time even when events arrive out of order, and keeps the last evaluation", () => {
    const view = timeline([
      event("evaluation.completed", { scores: { efficiency: 0.9 } }, "2026-10-10T10:00:09Z"),
      event("task.routed", { agent_id: "a1", candidates: [{}, {}] }, "2026-10-10T10:00:00Z", "t1"),
      event("approval.requested", { tool: "deploy" }, "2026-10-10T10:00:03Z"),
    ]);
    expect(view.routings).toEqual([{ at: "2026-10-10T10:00:00Z", task: "t1", agent: "a1", candidates: 2 }]);
    expect(view.approvals).toHaveLength(1);
    expect(view.evaluationScores).toEqual({ efficiency: 0.9 });
  });

  it("is empty for a mission with no events", () => {
    expect(timeline([]).totals.cacheHitRate).toBe(0);
  });
});
