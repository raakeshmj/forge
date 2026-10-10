import { Header } from "@/components/ui";
import { api } from "@/lib/api";
import type { AgentSummary } from "@/lib/types";

import { FirstAgent, LaunchForm, type AgentChoice } from "./LaunchForm";

export const dynamic = "force-dynamic";

const NEXT = [
  ["Plan", "one task, or a planner drafts a task graph"],
  ["Route", "each task goes to the fittest capable agent"],
  ["Run", "model calls and tools, through the security pipeline"],
  ["Evaluate", "scored on eight dimensions when it finishes"],
];

/** Planner agents can draft a task graph (`planner_agent_id`); told apart by role or model. */
function isPlanner(agent: AgentSummary): boolean {
  return /plan/i.test(agent.role) || /plan/i.test(agent.model);
}

export default async function NewMission() {
  const agents = await api<AgentSummary[]>("/agents");
  const choices: AgentChoice[] = agents.map((a) => ({
    id: a.id,
    name: a.name,
    role: a.role,
    model: a.model,
    planner: isPlanner(a),
  }));
  return (
    <>
      <Header
        eyebrow="Operate"
        title="New mission"
        sub="Give FORGE an objective. It plans, routes, runs, and evaluates the work; you watch it live."
      />
      {choices.some((a) => !a.planner) ? <LaunchForm agents={choices} /> : <FirstAgent />}
      <ol className="pipeline next-steps" aria-label="What happens next">
        {NEXT.map(([step, what], i) => (
          <li key={step}>
            <span className="mono">{String(i + 1).padStart(2, "0")}</span>
            <b>{step}</b>
            <span className="sub">{what}</span>
          </li>
        ))}
      </ol>
    </>
  );
}
