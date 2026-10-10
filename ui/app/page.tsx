import Link from "next/link";

import { Icon } from "@/components/icons";
import { MissionTable } from "@/components/MissionTable";
import { Bar, Columns, Distribution, Empty, Gauge, Header, Panel, Section, Stat } from "@/components/ui";
import { api } from "@/lib/api";
import { ms, num, pct, usd } from "@/lib/format";
import type { MissionSummary, Overview } from "@/lib/types";

export const dynamic = "force-dynamic";

export default async function Dashboard() {
  const [overview, missions] = await Promise.all([
    api<Overview>("/insights"),
    api<MissionSummary[]>("/missions?limit=10"),
  ]);
  const count = (key: string) => overview.missions_by_status.find((c) => c.key === key)?.count ?? 0;
  const totalMissions = overview.missions_by_status.reduce((t, c) => t + c.count, 0);
  const finished = count("COMPLETED") + count("FAILED") + count("CANCELLED");
  const executions = overview.executions_by_status.reduce((t, c) => t + c.count, 0);
  const executionsOk = overview.executions_by_status.find((c) => c.key === "COMPLETED")?.count ?? 0;
  const calls = overview.models.reduce((t, m) => t + m.calls, 0);
  const read = overview.models.reduce((t, m) => t + m.cache_read_input_tokens, 0);
  const prompt = overview.models.reduce(
    (t, m) => t + m.input_tokens + m.cache_read_input_tokens + m.cache_creation_input_tokens,
    0,
  );
  const savings = overview.models.reduce((t, m) => t + m.cache_savings_micro_usd, 0);
  const slowest = Math.max(0, ...overview.models.map((m) => m.latency_p95_ms));
  const topAgent = Math.max(1, ...overview.cost_by_agent.map((a) => a.cost_micro_usd));
  const running = count("RUNNING") + count("PLANNING") + count("READY");
  return (
    <>
      <Header
        eyebrow="Mission control"
        title="Dashboard"
        sub="Everything FORGE has run, read live from the system of record."
        aside={
          <Link href="/missions/new" className="button">
            <Icon name="launch" size={15} /> New mission
          </Link>
        }
      />

      <div className="hero">
        <Panel>
          <div className="panel-title">
            <span>
              <Icon name="missions" size={13} /> Missions
            </span>
            <Link href="/missions">all missions →</Link>
          </div>
          <div className="big-number">
            {num(totalMissions)}
            <small>{num(finished)} finished</small>
          </div>
          <div style={{ marginTop: 18 }}>
            <Distribution rows={overview.missions_by_status} />
          </div>
          <div className="stats" style={{ marginTop: 18 }}>
            <Stat label="Running" value={running} icon="bolt" tone={running ? "accent" : undefined} />
            <Stat
              label="Awaiting approval"
              value={<Link href="/approvals">{overview.pending_approvals}</Link>}
              icon="approvals"
              tone={overview.pending_approvals ? "wait" : undefined}
            />
            <Stat label="Completed" value={count("COMPLETED")} icon="approvals" tone="ok" />
            <Stat label="Failed" value={count("FAILED")} icon="policies" tone={count("FAILED") ? "bad" : undefined} />
          </div>
        </Panel>
        <Panel>
          <div className="panel-title">
            <span>
              <Icon name="pulse" size={13} /> System health
            </span>
            <Link href="/observability">observability →</Link>
          </div>
          <div className="hero-gauges">
            <Gauge
              ratio={prompt ? read / prompt : null}
              label="cache hit"
              reading={pct(prompt ? read / prompt : 0)}
              caption={`${num(read)} cached tokens`}
            />
            <Gauge
              ratio={finished ? count("COMPLETED") / finished : null}
              label="success"
              reading={finished ? pct(count("COMPLETED") / finished) : "—"}
              caption={`${count("COMPLETED")} of ${finished} finished`}
              tone="ok"
            />
            <Gauge
              ratio={executions ? executionsOk / executions : null}
              label="exec ok"
              reading={executions ? pct(executionsOk / executions) : "—"}
              caption={`${num(executions)} executions`}
              tone="wait"
            />
          </div>
        </Panel>
      </div>

      <div className="stats">
        <Stat label="Model calls" value={num(calls)} icon="layers" />
        <Stat label="Cache hit rate" value={pct(prompt ? read / prompt : 0)} icon="bolt" tone="accent" />
        <Stat label="Model p95 latency" value={ms(slowest)} icon="clock" />
        <Stat label="Total spend" value={usd(overview.total_cost_micro_usd)} icon="coin" />
        <Stat label="Saved by caching" value={usd(savings)} icon="spark" tone="ok" />
      </div>

      <div className="cols">
        <Section title="Spend by day" aside={<Link href="/costs">costs →</Link>}>
          <Panel>
            {overview.cost_by_day.length ? (
              <Columns rows={overview.cost_by_day.map((d) => ({ key: d.key, value: d.cost_micro_usd }))} format={usd} />
            ) : (
              <span className="empty-line mono">no spend yet</span>
            )}
          </Panel>
        </Section>
        <Section title="Spend by agent" aside={<Link href="/agents">agents →</Link>}>
          <Panel className="spend">
            {overview.cost_by_agent.length ? (
              overview.cost_by_agent.slice(0, 5).map((a) => (
                <div key={a.key}>
                  <div className="budget-label">
                    <span>{a.key}</span>
                    <b>{usd(a.cost_micro_usd)}</b>
                  </div>
                  <Bar ratio={a.cost_micro_usd / topAgent} />
                </div>
              ))
            ) : (
              <span className="empty-line mono">no spend yet</span>
            )}
          </Panel>
        </Section>
      </div>

      <Section title="Recent missions" aside={<Link href="/missions">view all →</Link>}>
        {missions.length === 0 ? (
          <Empty>No missions yet. Submit one with POST /missions.</Empty>
        ) : (
          <MissionTable missions={missions} />
        )}
      </Section>
    </>
  );
}
