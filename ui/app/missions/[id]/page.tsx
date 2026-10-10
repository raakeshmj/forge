import Link from "next/link";
import { notFound } from "next/navigation";

import { FlowGraph } from "@/components/FlowGraph";
import { LiveEvents } from "@/components/LiveEvents";
import { Bar, Empty, Header, Stat, Status } from "@/components/ui";
import { ApiError, api } from "@/lib/api";
import { ms, num, pct, score, short, usd, when } from "@/lib/format";
import { layout } from "@/lib/graph";
import { timeline } from "@/lib/timeline";
import type { AgentSummary, EvaluationView, EventView, MissionView } from "@/lib/types";

export const dynamic = "force-dynamic";

async function load(id: string) {
  try {
    return await Promise.all([
      api<MissionView>(`/missions/${id}`),
      api<EventView[]>(`/missions/${id}/events`),
      api<EvaluationView[]>(`/missions/${id}/evaluations`),
      api<AgentSummary[]>("/agents"),
    ]);
  } catch (error) {
    if (error instanceof ApiError && (error.status === 404 || error.status === 422)) notFound();
    throw error;
  }
}

export default async function MissionDetail({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const [mission, events, evaluations, agents] = await load(id);
  const view = timeline(events);
  const agentName = new Map(agents.map((a) => [a.id, a.name]));
  const taskKey = new Map(mission.tasks.map((t) => [t.id, t.key]));
  const executionTask = new Map(mission.executions.map((x) => [x.id, taskKey.get(x.task_id) ?? short(x.task_id)]));
  const planner = mission.tasks.find((t) => t.kind === "PLANNING");
  const edges = [
    ...mission.tasks.flatMap((t) => t.dependencies.map((d) => ({ from: d, to: t.id }))),
    // planned tasks were created by the planner: draw that provenance too
    ...(planner ? mission.tasks.filter((t) => t.kind === "WORK" && !t.dependencies.length).map((t) => ({ from: planner.id, to: t.id, kind: "planned" })) : []),
  ];
  const placed = layout(
    mission.tasks.map((t) => ({ id: t.id, label: `${t.key} (${t.kind.toLowerCase()})`, status: t.status })),
    edges,
  );
  const latest = evaluations.at(-1);
  const missionScores = latest?.results.filter((r) => r.scope === "mission") ?? [];
  const spent = mission.executions.reduce((t, x) => t + x.cost_micro_usd, 0);
  return (
    <>
      <Header
        title={mission.objective}
        sub={
          <>
            <Status value={mission.status} /> · mission <span className="mono">{mission.id}</span> · created{" "}
            {when(mission.created_at)} · max risk {mission.max_risk}
          </>
        }
      />
      <div className="stats">
        <Stat label="Spend / budget" value={`${usd(spent)} / ${usd(mission.budget_micro_usd)}`} />
        <Stat label="Model calls" value={view.modelCalls.length} />
        <Stat label="Tool calls" value={view.toolCalls.length} />
        <Stat label="Model latency (sum)" value={ms(view.totals.latencyMs)} />
        <Stat label="Tokens in / out" value={`${num(view.totals.inputTokens)} / ${num(view.totals.outputTokens)}`} />
        <Stat label="Cached tokens" value={num(view.totals.cacheReadTokens)} />
        <Stat label="Cache hit rate" value={pct(view.totals.cacheHitRate)} />
        <Stat label="Cache savings" value={usd(view.totals.savingsMicroUsd)} />
      </div>

      <h2>Plan and task graph</h2>
      {mission.tasks.length ? <FlowGraph nodes={placed} edges={edges} /> : <Empty>No tasks.</Empty>}
      <table>
        <thead>
          <tr>
            <th>Task</th>
            <th>Status</th>
            <th>Agent</th>
            <th>Capabilities</th>
            <th className="num">Attempts</th>
          </tr>
        </thead>
        <tbody>
          {mission.tasks.map((t) => (
            <tr key={t.id}>
              <td>{t.key}</td>
              <td>
                <Status value={t.status} />
              </td>
              <td>{t.agent_id ? <Link href={`/agents/${t.agent_id}`}>{agentName.get(t.agent_id) ?? short(t.agent_id)}</Link> : "unrouted"}</td>
              <td>{t.required_capabilities.join(", ") || "—"}</td>
              <td className="num">
                {t.attempts}/{t.max_attempts}
              </td>
            </tr>
          ))}
        </tbody>
      </table>

      <h2>Routing</h2>
      {view.routings.length ? (
        <table>
          <thead>
            <tr>
              <th>When</th>
              <th>Task</th>
              <th>Routed to</th>
              <th className="num">Candidates</th>
            </tr>
          </thead>
          <tbody>
            {view.routings.map((r) => (
              <tr key={`${r.task}-${r.at}`}>
                <td className="mono">{when(r.at)}</td>
                <td>{taskKey.get(r.task) ?? short(r.task)}</td>
                <td>{agentName.get(r.agent) ?? short(r.agent)}</td>
                <td className="num">{r.candidates}</td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : (
        <Empty>Single-agent mission: no routing decisions.</Empty>
      )}

      <h2>Model calls</h2>
      {view.modelCalls.length ? (
        <table>
          <thead>
            <tr>
              <th>Task</th>
              <th className="num">Turn</th>
              <th>Model</th>
              <th className="num">Latency</th>
              <th className="num">In</th>
              <th className="num">Out</th>
              <th className="num">Cache read</th>
              <th className="num">Cache write</th>
              <th className="num">Cost</th>
            </tr>
          </thead>
          <tbody>
            {view.modelCalls.map((c) => (
              <tr key={`${c.execution}-${c.turn}-${c.at}`}>
                <td>{executionTask.get(c.execution) ?? short(c.execution)}</td>
                <td className="num">{c.turn}</td>
                <td className="mono">{c.model}</td>
                <td className="num">{ms(c.latencyMs)}</td>
                <td className="num">{num(c.inputTokens)}</td>
                <td className="num">{num(c.outputTokens)}</td>
                <td className="num">{num(c.cacheReadTokens)}</td>
                <td className="num">{num(c.cacheWriteTokens)}</td>
                <td className="num">{usd(c.costMicroUsd)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : (
        <Empty>No model calls yet.</Empty>
      )}

      <h2>Tool calls</h2>
      {view.toolCalls.length ? (
        <table>
          <thead>
            <tr>
              <th>Task</th>
              <th>Tool</th>
              <th>Outcome</th>
              <th className="num">Latency</th>
              <th className="num">Attempts</th>
              <th className="num">Invocation</th>
              <th>Detail</th>
            </tr>
          </thead>
          <tbody>
            {view.toolCalls.map((c) => (
              <tr key={`${c.execution}-${c.toolUseId}-${c.outcome}`}>
                <td>{executionTask.get(c.execution) ?? short(c.execution)}</td>
                <td className="mono">{c.tool}</td>
                <td>
                  <span className={`badge s-${c.outcome}`}>{c.outcome}</span>
                </td>
                <td className="num">{ms(c.latencyMs)}</td>
                <td className="num">{c.attempts || "—"}</td>
                <td className="num">{c.invocation || "—"}</td>
                <td>{c.detail || "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : (
        <Empty>No tool calls.</Empty>
      )}

      {view.approvals.length ? (
        <>
          <h2>Approvals</h2>
          <ul>
            {view.approvals.map((a) => (
              <li key={a.id}>
                {when(a.occurred_at)} {a.event_type}{" "}
                {String(a.payload.tool ?? a.payload.decision ?? "")} {String(a.payload.decided_by ?? "")}
              </li>
            ))}
          </ul>
        </>
      ) : null}

      <h2>Results and evidence</h2>
      {mission.executions.filter((x) => x.output || x.failure_kind).length ? (
        mission.executions.map((x) => (
          <div key={x.id} style={{ marginBottom: 12 }}>
            <div className="sub">
              {executionTask.get(x.id) ?? short(x.id)} · <Status value={x.status} /> · attempt {x.attempt} ·{" "}
              {x.model_provider}/{x.model_name} · {usd(x.cost_micro_usd)}
              {x.failure_kind ? ` · ${x.failure_kind}: ${x.failure_detail ?? ""}` : ""}
            </div>
            {x.output ? <pre className="output">{x.output}</pre> : null}
          </div>
        ))
      ) : (
        <Empty>No results yet.</Empty>
      )}

      <h2>Evaluation</h2>
      {latest ? (
        <>
          <p className="sub">
            {latest.evaluator_set} · {when(latest.created_at)}
            {latest.benchmark_case ? ` · ground truth ${latest.benchmark_case}` : " · no ground truth"}
          </p>
          <table>
            <thead>
              <tr>
                <th>Dimension</th>
                <th className="num">Score</th>
                <th />
                <th className="num">Confidence</th>
                <th>Findings</th>
              </tr>
            </thead>
            <tbody>
              {missionScores.map((r) => (
                <tr key={r.dimension}>
                  <td>{r.dimension.replaceAll("_", " ")}</td>
                  <td className="num">{score(r.score)}</td>
                  <td>{r.score === null ? null : <Bar ratio={r.score} />}</td>
                  <td className="num">{score(r.confidence)}</td>
                  <td>{r.findings.map((f) => f.message).join("; ") || "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      ) : (
        <Empty>Not evaluated yet. Workers evaluate every finished mission.</Empty>
      )}

      <h2>Event history</h2>
      <LiveEvents
        missionId={mission.id}
        initial={events.map((e) => ({ id: e.id, type: e.event_type, at: e.occurred_at }))}
      />
    </>
  );
}
