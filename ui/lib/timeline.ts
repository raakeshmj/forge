// The mission timeline read model, derived from immutable events (ADR 0012). Pure.
import type { EventView } from "./types";

export interface ModelCall {
  at: string;
  execution: string;
  turn: number;
  model: string;
  latencyMs: number;
  inputTokens: number;
  outputTokens: number;
  cacheReadTokens: number;
  cacheWriteTokens: number;
  costMicroUsd: number;
  savingsMicroUsd: number;
  attempts: number;
}

export interface ToolCall {
  at: string;
  execution: string;
  toolUseId: string;
  tool: string;
  outcome: "completed" | "error" | "failed" | "denied" | "pending";
  latencyMs: number | null;
  attempts: number;
  invocation: number; // > 1: re-executed after a crash (idempotent tools only)
  detail: string;
}

export interface Routing {
  at: string;
  task: string;
  agent: string;
  candidates: number;
}

export interface Timeline {
  modelCalls: ModelCall[];
  toolCalls: ToolCall[];
  routings: Routing[];
  approvals: EventView[];
  evaluationScores: Record<string, number | null> | null;
  totals: {
    latencyMs: number;
    inputTokens: number;
    outputTokens: number;
    cacheReadTokens: number;
    cacheWriteTokens: number;
    costMicroUsd: number;
    savingsMicroUsd: number;
    cacheHitRate: number;
  };
}

const n = (value: unknown): number => (typeof value === "number" ? value : 0);
const s = (value: unknown): string => (typeof value === "string" ? value : "");

export function timeline(events: readonly EventView[]): Timeline {
  const ordered = [...events].sort(
    (a, b) => a.occurred_at.localeCompare(b.occurred_at) || a.sequence - b.sequence,
  );
  const modelCalls: ModelCall[] = [];
  const calls = new Map<string, ToolCall>();
  const toolCalls: ToolCall[] = [];
  const routings: Routing[] = [];
  const approvals: EventView[] = [];
  let evaluationScores: Record<string, number | null> | null = null;

  for (const event of ordered) {
    const p = event.payload;
    switch (event.event_type) {
      case "model.called": {
        const usage = (p.usage ?? {}) as Record<string, unknown>;
        modelCalls.push({
          at: event.occurred_at,
          execution: event.aggregate_id,
          turn: n(p.turn),
          model: `${s(p.provider)}/${s(p.model)}`,
          latencyMs: n(p.latency_ms),
          inputTokens: n(usage.input_tokens),
          outputTokens: n(usage.output_tokens),
          cacheReadTokens: n(usage.cache_read_input_tokens),
          cacheWriteTokens: n(usage.cache_creation_input_tokens),
          costMicroUsd: n(p.cost_micro_usd),
          savingsMicroUsd: n(p.cache_savings_micro_usd),
          attempts: n(p.attempts),
        });
        break;
      }
      case "tool.called": {
        const call: ToolCall = {
          at: event.occurred_at,
          execution: event.aggregate_id,
          toolUseId: s(p.tool_use_id),
          tool: s(p.tool),
          outcome: "pending",
          latencyMs: null,
          attempts: 0,
          invocation: n(p.invocation),
          detail: "",
        };
        calls.set(`${event.aggregate_id}:${call.toolUseId}`, call);
        toolCalls.push(call);
        break;
      }
      case "tool.completed":
      case "tool.failed": {
        const call = calls.get(`${event.aggregate_id}:${s(p.tool_use_id)}`);
        if (call) {
          call.outcome =
            event.event_type === "tool.failed" ? "failed" : p.is_error ? "error" : "completed";
          call.latencyMs = Date.parse(event.occurred_at) - Date.parse(call.at);
          call.attempts = n(p.attempts);
          call.detail = s(p.error_type);
        }
        break;
      }
      case "tool.denied":
        toolCalls.push({
          at: event.occurred_at,
          execution: event.aggregate_id,
          toolUseId: s(p.tool_use_id),
          tool: s(p.tool),
          outcome: "denied",
          latencyMs: null,
          attempts: 0,
          invocation: 0,
          detail: `${s(p.stage)}: ${s(p.reason)}`,
        });
        break;
      case "task.routed":
        routings.push({
          at: event.occurred_at,
          task: event.aggregate_id,
          agent: s(p.agent_id),
          candidates: Array.isArray(p.candidates) ? p.candidates.length : 0,
        });
        break;
      case "approval.requested":
      case "approval.completed":
        approvals.push(event);
        break;
      case "evaluation.completed":
        evaluationScores = (p.scores ?? null) as Record<string, number | null> | null;
        break;
    }
  }

  const sum = (pick: (call: ModelCall) => number) => modelCalls.reduce((t, c) => t + pick(c), 0);
  const prompt = sum((c) => c.inputTokens + c.cacheReadTokens + c.cacheWriteTokens);
  return {
    modelCalls,
    toolCalls,
    routings,
    approvals,
    evaluationScores,
    totals: {
      latencyMs: sum((c) => c.latencyMs),
      inputTokens: sum((c) => c.inputTokens),
      outputTokens: sum((c) => c.outputTokens),
      cacheReadTokens: sum((c) => c.cacheReadTokens),
      cacheWriteTokens: sum((c) => c.cacheWriteTokens),
      costMicroUsd: sum((c) => c.costMicroUsd),
      savingsMicroUsd: sum((c) => c.savingsMicroUsd),
      cacheHitRate: prompt ? sum((c) => c.cacheReadTokens) / prompt : 0,
    },
  };
}
