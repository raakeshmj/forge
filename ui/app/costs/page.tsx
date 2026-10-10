import { Bar, Empty, Header, Stat } from "@/components/ui";
import { api } from "@/lib/api";
import { num, usd } from "@/lib/format";
import type { CostShare, MissionSummary, Overview } from "@/lib/types";

export const dynamic = "force-dynamic";

function Shares({ title, rows, unit }: { title: string; rows: CostShare[]; unit: string }) {
  const top = Math.max(1, ...rows.map((r) => r.cost_micro_usd));
  return (
    <div>
      <h2>{title}</h2>
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
                <td><Bar ratio={r.cost_micro_usd / top} /></td>
                <td className="num">{num(r.executions)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : (
        <Empty>No spend.</Empty>
      )}
    </div>
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
      <Header title="Costs" sub="Spend from provider-reported usage and tool costs, in exact micro-USD." />
      <div className="stats">
        <Stat label="Total spend" value={usd(o.total_cost_micro_usd)} />
        <Stat label="Saved by caching" value={usd(savings)} />
        <Stat label="Completed missions" value={completed.length} />
        <Stat
          label="Cost per completed mission"
          value={completed.length ? usd(Math.round(completedSpend / completed.length)) : "—"}
        />
      </div>
      <div className="cols">
        <Shares title="By agent" rows={o.cost_by_agent} unit="Agent" />
        <Shares title="By model (model calls only)" rows={byModel} unit="Model" />
      </div>
      <Shares title="By day" rows={o.cost_by_day} unit="Day (UTC)" />
    </>
  );
}
