"use server";

import { revalidatePath } from "next/cache";
import { redirect } from "next/navigation";

import { apiCreate } from "@/lib/api";
import { MAX_BUDGET_USD, RISKS } from "@/lib/launch";

export interface LaunchState {
  ok: boolean;
  message: string;
}

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;

/**
 * Submit a mission exactly as any API client would (POST /missions, ADR 0012), then open it.
 * `target` is `agent:<id>` (one task) or `planner:<id>` (the planner drafts a task graph).
 */
export async function launch(_previous: LaunchState | null, form: FormData): Promise<LaunchState> {
  const objective = String(form.get("objective") ?? "").trim();
  const [mode, id] = String(form.get("target") ?? "").split(":");
  const budgetUsd = Number(form.get("budget_usd"));
  const risk = String(form.get("max_risk") ?? "LOW");
  if (!objective) return { ok: false, message: "Describe what the mission should achieve." };
  if (objective.length > 10_000)
    return { ok: false, message: "The objective is too long (10,000 characters at most)." };
  if (!id || !UUID.test(id) || (mode !== "agent" && mode !== "planner")) {
    return { ok: false, message: "Choose an agent to run the mission." };
  }
  if (!Number.isFinite(budgetUsd) || budgetUsd <= 0 || budgetUsd > MAX_BUDGET_USD) {
    return { ok: false, message: `The budget must be more than $0 and at most $${MAX_BUDGET_USD}.` };
  }
  if (!(RISKS as readonly string[]).includes(risk)) return { ok: false, message: "Choose a risk ceiling." };

  const { status, detail, body } = await apiCreate("/missions", {
    objective,
    [mode === "planner" ? "planner_agent_id" : "agent_id"]: id,
    budget_micro_usd: Math.round(budgetUsd * 1_000_000),
    max_risk: risk,
  });
  const missionId = (body as { id?: unknown } | undefined)?.id;
  if (status !== 201 || typeof missionId !== "string" || !UUID.test(missionId)) {
    return { ok: false, message: `Not launched (${status}): ${detail}` };
  }
  revalidatePath("/missions");
  redirect(`/missions/${missionId}`);
}

/** The empty state's one-click start: the same demo agent RUNNING.md registers with curl. */
export async function registerDemoAgent(): Promise<LaunchState> {
  const { status, detail } = await apiCreate("/agents", {
    name: "investigator",
    role: "incident investigator",
    instructions: "Investigate with the tools available.",
    model: { provider: "demo", name: "demo-investigator-1" },
    tools: ["metrics.read"],
    capabilities: ["incident-response"],
  });
  revalidatePath("/missions/new");
  if (status === 201) return { ok: true, message: "Registered the demo investigator." };
  return { ok: false, message: `Not registered (${status}): ${detail}` };
}
