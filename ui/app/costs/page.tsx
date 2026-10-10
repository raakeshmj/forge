import { Bar, Columns, Empty, Header, Panel, Section, Stat } from "@/components/ui";
import { api } from "@/lib/api";
import { num, usd } from "@/lib/format";
import type { CostShare, MissionSummary, Overview } from "@/lib/types";

export const dynamic = "force-dynamic";

function Shares({ title, rows, unit }: { title: string; rows: CostShare[]; unit: string }) {
  const top = Math.max(1, ...rows.map((r) => r.cost_micro_usd));
  return (
    <Section title={title}>
      {rows.length ? (
        <table>
          <thead>
            <tr>
              <th>{unit}</th>
              <th className="num">Cost</th>
              <th />
              <th className="num">Executions</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.key}>
                <td>{r.key}</td>
                <td className="num">{usd(r.cost_micro_usd)}</td>
                <td>
                  <Bar ratio={r.cost_micro_usd / top} />
                </td>
                <td className="num">{num(r.executions)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : (
        <Empty>No spend.</Empty>
      )}
    </Section>
  );
}

export default async function Costs() {
  const [o, missions] = await Promise.all([api<Overview>("/insights"), api<MissionSummary[]>("/missions?limit=500")]);
  const completed = missions.filter((m) => m.status === "COMPLETED");
  const completedSpend = completed.reduce((t, m) => t + m.cost_micro_usd, 0);
  const savings = o.models.reduce((t, m) => t + m.cache_savings_micro_usd, 0);
  const byModel: CostShare[] = o.models.map((m) => ({
    key: `${m.provider}/${m.model}`,
    cost_micro_usd: m.cost_micro_usd,
    executions: m.calls,
  }));
  return (
    <>
      <Header
        eyebrow="Measure"
        title="Costs"
        sub="Spend from provider-reported usage and tool costs, in exact micro-USD."
      />
      <div className="stats">
        <Stat label="Total spend" value={usd(o.total_cost_micro_usd)} icon="coin" tone="accent" />
        <Stat label="Saved by caching" value={usd(savings)} icon="spark" tone={savings > 0 ? "ok" : undefined} />
        <Stat label="Completed missions" value={completed.length} icon="missions" />
        <Stat
          label="Cost per completed mission"
          value={completed.length ? usd(Math.round(completedSpend / completed.length)) : "—"}
          icon="coin"
        />
      </div>
      <Section title="Spend over time" aside="UTC days">
        <Panel>
          {o.cost_by_day.length ? (
            <Columns
              rows={o.cost_by_day.map((d) => ({ key: d.key, value: d.cost_micro_usd }))}
              format={usd}
              height={140}
            />
          ) : (
            <span className="empty-line mono">no spend yet</span>
          )}
        </Panel>
      </Section>
      <div className="cols">
        <Shares title="By agent" rows={o.cost_by_agent} unit="Agent" />
        <Shares title="By model (model calls only)" rows={byModel} unit="Model" />
      </div>
      <Shares title="By day" rows={o.cost_by_day} unit="Day (UTC)" />
    </>
  );
}
