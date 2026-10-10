import Link from "next/link";
import { notFound } from "next/navigation";

import { FlowGraph } from "@/components/FlowGraph";
import { LiveEvents } from "@/components/LiveEvents";
import { Bar, Empty, Header, Meter, Section, Stat, Status } from "@/components/ui";
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
    ...(planner
      ? mission.tasks
          .filter((t) => t.kind === "WORK" && !t.dependencies.length)
          .map((t) => ({ from: planner.id, to: t.id, kind: "planned" }))
      : []),
  ];
  const placed = layout(
    mission.tasks.map((t) => ({ id: t.id, label: `${t.key} (${t.kind.toLowerCase()})`, status: t.status })),
    edges,
  );
  const latest = evaluations.at(-1);
  const missionScores = latest?.results.filter((r) => r.scope === "mission") ?? [];
  const spent = mission.executions.reduce((t, x) => t + x.cost_micro_usd, 0);
  const used = mission.budget_micro_usd ? spent / mission.budget_micro_usd : 0;
  return (
    <>
      <Header
        eyebrow={`Mission · ${short(mission.id)}`}
        title={mission.objective}
        sub={
          <>
            <Status value={mission.status} />
            <span className="chip">
              created <b>{when(mission.created_at)}</b>
            </span>
            <span className="chip">
              max risk <b>{mission.max_risk}</b>
            </span>
            <span className="chip">
              tasks <b>{mission.tasks.length}</b>
            </span>
            <span className="chip mono">mission {mission.id}</span>
          </>
        }
      />
      <div className="panel hud budget" style={{ marginBottom: 14 }}>
        <div className="budget-label">
          <span>Spend / budget</span>
          <b>
            {usd(spent)} / {usd(mission.budget_micro_usd)} · {pct(used)}
          </b>
        </div>
        <Bar ratio={used} tone={used > 0.9 ? "bad" : "accent"} />
      </div>
      <div className="stats four">
        <Stat label="Executions" value={mission.executions.length} icon="missions" />
        <Stat label="Model calls" value={view.modelCalls.length} icon="layers" />
        <Stat label="Tool calls" value={view.toolCalls.length} icon="bolt" />
        <Stat label="Model latency (sum)" value={ms(view.totals.latencyMs)} icon="clock" />
        <Stat
          label="Tokens in / out"
          value={`${num(view.totals.inputTokens)} / ${num(view.totals.outputTokens)}`}
          icon="coin"
        />
        <Stat label="Cached tokens" value={num(view.totals.cacheReadTokens)} icon="spark" />
        <Stat label="Cache hit rate" value={pct(view.totals.cacheHitRate)} icon="bolt" tone="accent" />
        <Stat
          label="Cache savings"
          value={usd(view.totals.savingsMicroUsd)}
          icon="coin"
          tone={view.totals.savingsMicroUsd > 0 ? "ok" : view.totals.savingsMicroUsd < 0 ? "warn" : undefined}
          hint={view.totals.savingsMicroUsd < 0 ? "cache writes cost more than reads saved" : undefined}
        />
      </div>

      <Section
        title="Plan and task graph"
        aside={`${mission.tasks.length} ${mission.tasks.length === 1 ? "task" : "tasks"}`}
      >
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
                <td>
                  <b>{t.key}</b> <span className="id-chip">{t.kind.toLowerCase()}</span>
                </td>
                <td>
                  <Status value={t.status} />
                </td>
                <td>
                  {t.agent_id ? (
                    <Link href={`/agents/${t.agent_id}`}>{agentName.get(t.agent_id) ?? short(t.agent_id)}</Link>
                  ) : (
                    "unrouted"
                  )}
                </td>
                <td>
                  <span className="tags">
                    {t.required_capabilities.length
                      ? t.required_capabilities.map((c) => (
                          <span key={c} className="tag cyan">
                            {c}
                          </span>
                        ))
                      : "—"}
                  </span>
                </td>
                <td className="num">
                  {t.attempts}/{t.max_attempts}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </Section>

      <Section title="Routing" aside="capability-aware assignment">
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
                  <td>
                    <Link href={`/agents/${r.agent}`}>{agentName.get(r.agent) ?? short(r.agent)}</Link>
                  </td>
                  <td className="num">{r.candidates}</td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : (
          <Empty>Single-agent mission: no routing decisions.</Empty>
        )}
      </Section>

      <Section title="Model calls" aside="latency, tokens, cache, cost per call">
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
                  <td>
                    <span className="tag violet">{c.model}</span>
                  </td>
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
      </Section>

      <Section title="Tool calls" aside="every call passed the security pipeline">
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
                  <td>
                    <span className="tag cyan">{c.tool}</span>
                  </td>
                  <td>
                    <Status value={c.outcome} />
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
      </Section>

      {view.approvals.length ? (
        <Section title="Approvals" aside="human decisions on high-risk calls">
          <table>
            <tbody>
              {view.approvals.map((a) => (
                <tr key={a.id}>
                  <td className="mono">{when(a.occurred_at)}</td>
                  <td>
                    <span className="tag violet">{a.event_type}</span>
                  </td>
                  <td>{String(a.payload.tool ?? a.payload.decision ?? "")}</td>
                  <td>{String(a.payload.decided_by ?? "")}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Section>
      ) : null}

      <Section title="Results and evidence">
        {mission.executions.filter((x) => x.output || x.failure_kind).length ? (
          mission.executions.map((x) => (
            <div key={x.id} className="result">
              <div className="result-head">
                <b>{executionTask.get(x.id) ?? short(x.id)}</b>
                <Status value={x.status} />
                <span className="chip">attempt {x.attempt}</span>
                <span className="chip mono">
                  {x.model_provider}/{x.model_name}
                </span>
                <span className="chip mono">{usd(x.cost_micro_usd)}</span>
                {x.failure_kind ? (
                  <span className="failure">
                    {x.failure_kind}: {x.failure_detail ?? ""}
                  </span>
                ) : null}
              </div>
              {x.output ? <pre className="output">{x.output}</pre> : null}
            </div>
          ))
        ) : (
          <Empty>No results yet.</Empty>
        )}
      </Section>

      <Section
        title="Evaluation"
        aside={
          latest
            ? `${latest.evaluator_set} · ${latest.benchmark_case ? `ground truth ${latest.benchmark_case}` : "no ground truth"}`
            : undefined
        }
      >
        {latest ? (
          <table>
            <thead>
              <tr>
                <th>Dimension</th>
                <th>Score</th>
                <th className="num">Confidence</th>
                <th>Findings</th>
              </tr>
            </thead>
            <tbody>
              {missionScores.map((r) => (
                <tr key={r.dimension}>
                  <td>{r.dimension.replaceAll("_", " ")}</td>
                  <td>
                    <Meter value={r.score} />
                  </td>
                  <td className="num">{score(r.confidence)}</td>
                  <td>{r.findings.map((f) => f.message).join("; ") || "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : (
          <Empty>Not evaluated yet. Workers evaluate every finished mission.</Empty>
        )}
      </Section>

      <Section title="Event history" aside="immutable, streamed live">
        <LiveEvents
          missionId={mission.id}
          initial={events.map((e) => ({ id: e.id, type: e.event_type, at: e.occurred_at }))}
        />
      </Section>
    </>
  );
}
