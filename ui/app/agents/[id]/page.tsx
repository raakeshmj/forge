import { notFound } from "next/navigation";

import { Bar, Empty, Header, Stat } from "@/components/ui";
import { ApiError, api } from "@/lib/api";
import { ms, num, pct, score, usd, when } from "@/lib/format";
import type { AgentDetail, AgentUsage } from "@/lib/types";

export const dynamic = "force-dynamic";

export default async function AgentPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  let agent: AgentDetail;
  let usage: AgentUsage;
  try {
    [agent, usage] = await Promise.all([
      api<AgentDetail>(`/agents/${id}`),
      api<AgentUsage>(`/agents/${id}/usage`),
    ]);
  } catch (error) {
    if (error instanceof ApiError && (error.status === 404 || error.status === 422)) notFound();
    throw error;
  }
  const r = agent.reliability;
  return (
    <>
      <Header
        title={agent.name}
        sub={
          <>
            v{agent.version} · {agent.role} · <span className="mono">{agent.model}</span>
            {agent.fallback_models.length ? ` (fallback ${agent.fallback_models.join(", ")})` : ""}
          </>
        }
      />
      <div className="stats">
        <Stat label="Reliability" value={`${score(r.estimate)} ± ${score(r.uncertainty)}`} />
        <Stat label="Succeeded / failed" value={`${r.successes} / ${r.failures}`} />
        <Stat label="Executions" value={usage.executions} />
        <Stat label="Total cost" value={usd(usage.cost_micro_usd)} />
        <Stat label="Mean cost per execution" value={r.mean_cost_micro_usd === null ? "—" : usd(r.mean_cost_micro_usd)} />
        <Stat label="Mean latency" value={ms(r.mean_latency_ms)} />
      </div>

      <h2>Competency</h2>
      {agent.competencies.length ? (
        <table>
          <thead>
            <tr>
              <th>Capability</th>
              <th className="num">Estimate</th>
              <th />
              <th className="num">Uncertainty</th>
              <th className="num">Samples</th>
              <th className="num">Trend</th>
              <th className="num">Calibration</th>
              <th>Last evaluated</th>
            </tr>
          </thead>
          <tbody>
            {agent.competencies.map((c) => (
              <tr key={c.capability}>
                <td>{c.capability}</td>
                <td className="num">{score(c.estimated_score)}</td>
                <td>
                  <Bar ratio={c.estimated_score} title={`±${c.uncertainty}`} />
                </td>
                <td className="num">±{score(c.uncertainty)}</td>
                <td className="num">
                  {c.sample_count} ({c.effective_samples.toFixed(1)} eff.)
                </td>
                <td className="num">{c.recent_trend === null ? "—" : c.recent_trend.toFixed(2)}</td>
                <td className="num">{score(c.calibration)}</td>
                <td className="mono">{when(c.last_evaluated)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : (
        <Empty>No measured competency yet: routing relies on declared capabilities until evaluations arrive.</Empty>
      )}

      <div className="cols">
        <div>
          <h2>Evaluation dimensions</h2>
          {usage.dimensions.length ? (
            <table>
              <tbody>
                {usage.dimensions.map((d) => (
                  <tr key={d.dimension}>
                    <td>{d.dimension.replaceAll("_", " ")}</td>
                    <td className="num">{score(d.mean)}</td>
                    <td>
                      <Bar ratio={d.mean} />
                    </td>
                    <td className="num">n={d.samples}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : (
            <Empty>No scored evaluations.</Empty>
          )}
        </div>
        <div>
          <h2>Failure clusters</h2>
          {usage.failures_by_kind.length || usage.findings.length ? (
            <table>
              <tbody>
                {usage.failures_by_kind.map((f) => (
                  <tr key={`k-${f.key}`}>
                    <td>execution: {f.key}</td>
                    <td className="num">{f.count}</td>
                  </tr>
                ))}
                {usage.findings.map((f) => (
                  <tr key={`f-${f.key}`}>
                    <td>finding: {f.key}</td>
                    <td className="num">{f.count}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : (
            <Empty>No failures recorded.</Empty>
          )}
        </div>
      </div>

      <h2>Model usage</h2>
      {usage.models.length ? (
        <table>
          <thead>
            <tr>
              <th>Model</th>
              <th className="num">Calls</th>
              <th className="num">p50 / p95</th>
              <th className="num">Tokens in / out</th>
              <th className="num">Cache hit</th>
              <th className="num">Cost</th>
            </tr>
          </thead>
          <tbody>
            {usage.models.map((m) => (
              <tr key={`${m.provider}/${m.model}`}>
                <td className="mono">
                  {m.provider}/{m.model}
                </td>
                <td className="num">{num(m.calls)}</td>
                <td className="num">
                  {ms(m.latency_p50_ms)} / {ms(m.latency_p95_ms)}
                </td>
                <td className="num">
                  {num(m.input_tokens)} / {num(m.output_tokens)}
                </td>
                <td className="num">{pct(m.cache_hit_rate)}</td>
                <td className="num">{usd(m.cost_micro_usd)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : (
        <Empty>No model calls.</Empty>
      )}

      <h2>Configuration</h2>
      <div className="cols">
        <div className="panel">
          <div className="sub">Tools</div>
          <div className="mono">{agent.tools.join(", ") || "—"}</div>
          <div className="sub" style={{ marginTop: 10 }}>Capabilities</div>
          <div>{agent.capabilities.join(", ") || "—"}</div>
          <div className="sub" style={{ marginTop: 10 }}>Pinned skills</div>
          <div className="mono">{agent.skills.join(", ") || "—"}</div>
        </div>
        <pre className="output">{agent.instructions}</pre>
      </div>
    </>
  );
}
