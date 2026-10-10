import Link from "next/link";

import { Icon } from "@/components/icons";
import { Empty, Header, Meter, Section, Status } from "@/components/ui";
import { api } from "@/lib/api";
import { short, usd, when } from "@/lib/format";
import type { AgentSummary, BenchmarkView, RunView } from "@/lib/types";

export const dynamic = "force-dynamic";

function summary(run: RunView): string {
  const s = run.summary;
  const parts: string[] = [];
  const ece = s.expected_calibration_error;
  if (typeof ece === "number") parts.push(`ECE ${ece.toFixed(3)}`);
  if (typeof s.cost_per_success_micro_usd === "number") parts.push(`${usd(s.cost_per_success_micro_usd)} per success`);
  if (typeof s.cases === "number") parts.push(`${s.cases} cases`);
  return parts.join(" · ") || "—";
}

export default async function Benchmarks() {
  const [benchmarks, runs, agents] = await Promise.all([
    api<BenchmarkView[]>("/benchmarks"),
    api<RunView[]>("/benchmark-runs?limit=100"),
    api<AgentSummary[]>("/agents"),
  ]);
  const agentName = new Map(agents.map((a) => [a.id, a.name]));
  return (
    <>
      <Header
        eyebrow="Measure"
        title="Benchmarks"
        sub="Versioned datasets with ground truth, and runs of agents against them."
      />
      <div className="cards">
        {benchmarks.map((b) => {
          const mine = runs.filter((r) => r.benchmark === b.name && r.version === b.version);
          return (
            <div key={`${b.name}@${b.version}`} className="card hud">
              <div className="card-title">
                <span className="agent-name">
                  <Icon name="benchmarks" />
                  {b.name}@{b.version}
                </span>
                <span className="tag cyan">{b.capability}</span>
              </div>
              <div className="card-body">{b.description}</div>
              <div className="card-foot mono">
                {b.cases.length} cases · {mine.length} runs
              </div>
            </div>
          );
        })}
      </div>
      <Section title="Runs" aside="each run is scored against ground truth">
        {runs.length ? (
          <table>
            <thead>
              <tr>
                <th>Run</th>
                <th>Benchmark</th>
                <th>Agent</th>
                <th>Status</th>
                <th>Pass rate</th>
                <th>Summary</th>
                <th>Started</th>
              </tr>
            </thead>
            <tbody>
              {runs.map((r) => (
                <tr key={r.id}>
                  <td className="mono">{short(r.id)}</td>
                  <td>
                    {r.benchmark}@{r.version}
                  </td>
                  <td>
                    <Link href={`/agents/${r.agent_id}`}>{agentName.get(r.agent_id) ?? short(r.agent_id)}</Link>
                  </td>
                  <td>
                    <Status value={r.status} />
                  </td>
                  <td>
                    <Meter value={typeof r.summary.pass_rate === "number" ? r.summary.pass_rate : null} />
                  </td>
                  <td>{summary(r)}</td>
                  <td className="mono">{when(r.created_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : (
          <Empty>No benchmark runs. Start one with POST /benchmarks/&#123;name&#125;/runs.</Empty>
        )}
      </Section>
    </>
  );
}
