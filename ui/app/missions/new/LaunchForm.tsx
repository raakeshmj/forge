"use client";

import { useActionState, useRef, useState } from "react";

import { Icon } from "@/components/icons";
import { EXAMPLES, MAX_BUDGET_USD, RISKS } from "@/lib/launch";

import { launch, registerDemoAgent, type LaunchState } from "./actions";

export interface AgentChoice {
  id: string;
  name: string;
  role: string;
  model: string;
  planner: boolean;
}

const RISK_HINT: Record<(typeof RISKS)[number], string> = {
  LOW: "read-only tools only",
  MEDIUM: "tools a policy allows",
  HIGH: "risky calls wait for your approval",
};

export function LaunchForm({ agents }: { agents: AgentChoice[] }) {
  const [state, action, pending] = useActionState<LaunchState | null, FormData>(launch, null);
  const [objective, setObjective] = useState("");
  const [risk, setRisk] = useState<(typeof RISKS)[number]>("LOW");
  const form = useRef<HTMLFormElement>(null);
  const planners = agents.filter((a) => a.planner);
  const workers = agents.filter((a) => !a.planner);

  return (
    <form ref={form} action={action} className="composer panel hud">
      <div className="composer-main">
        <label htmlFor="objective" className="kv-label">
          Objective
        </label>
        <textarea
          id="objective"
          name="objective"
          value={objective}
          onChange={(event) => setObjective(event.target.value)}
          onKeyDown={(event) => {
            if ((event.metaKey || event.ctrlKey) && event.key === "Enter") form.current?.requestSubmit();
          }}
          placeholder="What should FORGE find out or do?"
          rows={6}
          maxLength={10_000}
          required
          autoFocus
        />
        <div className="examples">
          <span className="kv-label">Try</span>
          {EXAMPLES.map((example) => (
            <button key={example} type="button" className="example" onClick={() => setObjective(example)}>
              {example}
            </button>
          ))}
        </div>
      </div>

      <div className="composer-side">
        <div>
          <label htmlFor="target" className="kv-label">
            Run with
          </label>
          <select id="target" name="target" required defaultValue={workers[0] ? `agent:${workers[0].id}` : ""}>
            {workers.length ? (
              <optgroup label="One agent does the whole mission">
                {workers.map((a) => (
                  <option key={a.id} value={`agent:${a.id}`}>
                    {a.name} · {a.role}
                  </option>
                ))}
              </optgroup>
            ) : null}
            {planners.length ? (
              <optgroup label="A planner splits it into a task graph">
                {planners.map((a) => (
                  <option key={a.id} value={`planner:${a.id}`}>
                    {a.name} · plans, then routes tasks
                  </option>
                ))}
              </optgroup>
            ) : null}
          </select>
        </div>

        <div>
          <label htmlFor="budget_usd" className="kv-label">
            Budget
          </label>
          <div className="money">
            <span>$</span>
            <input
              id="budget_usd"
              name="budget_usd"
              type="number"
              min="0.001"
              max={MAX_BUDGET_USD}
              step="0.001"
              defaultValue="0.10"
              required
            />
          </div>
        </div>

        <fieldset className="segmented">
          <legend className="kv-label">Risk ceiling</legend>
          <div className="segments">
            {RISKS.map((level) => (
              <label key={level} className={risk === level ? `on r-${level}` : `r-${level}`}>
                <input
                  type="radio"
                  name="max_risk"
                  value={level}
                  checked={risk === level}
                  onChange={() => setRisk(level)}
                />
                {level.toLowerCase()}
              </label>
            ))}
          </div>
          <div className="segment-hint">{RISK_HINT[risk]}</div>
        </fieldset>

        <button type="submit" className="launch" disabled={pending}>
          {pending ? "Launching…" : "Launch mission"}
          <Icon name="arrow" size={16} />
        </button>
        <div className="launch-hint mono">
          <kbd>⌘</kbd> <kbd>↵</kbd> to launch
        </div>
        {state && !state.ok ? <div className="notice bad">{state.message}</div> : null}
      </div>
    </form>
  );
}

/** Shown when no agent exists yet: registers the demo agent with one click. */
export function FirstAgent() {
  const [state, action, pending] = useActionState<LaunchState | null>(registerDemoAgent, null);
  return (
    <form action={action} className="panel hud first-agent">
      <div className="agent-name">
        <Icon name="agents" />
        No agents yet
      </div>
      <p className="sub">
        A mission needs an agent to run it. Register the demo investigator: it uses FORGE&apos;s deterministic local
        model and the simulated metrics tool, so it costs nothing and needs no API key.
      </p>
      <button type="submit" className="launch" disabled={pending}>
        {pending ? "Registering…" : "Register the demo investigator"}
        <Icon name="arrow" size={16} />
      </button>
      {state ? <div className={`notice ${state.ok ? "ok" : "bad"}`}>{state.message}</div> : null}
    </form>
  );
}
