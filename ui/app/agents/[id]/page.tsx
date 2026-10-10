import { notFound } from "next/navigation";

import { Empty, Gauge, Header, Meter, Panel, Section, Stat } from "@/components/ui";
import { ApiError, api } from "@/lib/api";
import { ms, num, score, usd, when } from "@/lib/format";
import type { AgentDetail, AgentUsage } from "@/lib/types";

export const dynamic = "force-dynamic";

export default async function AgentPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  let agent: AgentDetail;
  let usage: AgentUsage;
  try {
    [agent, usage] = await Promise.all([api<AgentDetail>(`/agents/${id}`), api<AgentUsage>(`/agents/${id}/usage`)]);
  } catch (error) {
    if (error instanceof ApiError && (error.status === 404 || error.status === 422)) notFound();
    throw error;
  }
  const r = agent.reliability;
  return (
    <>
      <Header
        eyebrow={`Agent · v${agent.version}`}
        title={agent.name}
        sub={
          <>
            <span>{agent.role}</span>
            <span className="tag violet">{agent.model}</span>
            {agent.fallback_models.length ? (
              <span className="chip">
                fallback <b>{agent.fallback_models.join(", ")}</b>
              </span>
            ) : null}
          </>
        }
      />

      <div className="hero">
        <Panel>
          <div className="panel-title">
            <span>Reliability and spend</span>
          </div>
          <div className="stats">
            <Stat
              label="Reliability"
              value={`${score(r.estimate)} ± ${score(r.uncertainty)}`}
              icon="shield"
              tone="accent"
            />
            <Stat label="Succeeded / failed" value={`${r.successes} / ${r.failures}`} icon="approvals" />
            <Stat label="Executions" value={usage.executions} icon="layers" />
            <Stat label="Total cost" value={usd(usage.cost_micro_usd)} icon="coin" />
            <Stat
              label="Mean cost per execution"
              value={r.mean_cost_micro_usd === null ? "—" : usd(r.mean_cost_micro_usd)}
              icon="coin"
            />
            <Stat label="Mean latency" value={ms(r.mean_latency_ms)} icon="clock" />
          </div>
        </Panel>
        <Panel>
          <div className="panel-title">
            <span>Estimated reliability</span>
            <span className="mono">± uncertainty</span>
          </div>
          <div className="hero-gauges">
            <Gauge
              ratio={r.estimate}
              label="reliability"
              reading={score(r.estimate)}
              caption={`± ${score(r.uncertainty)} from ${r.successes + r.failures} executions`}
              tone="ok"
              size={160}
            />
          </div>
        </Panel>
      </div>

      <Section title="Competency" aside="per capability, measured from evaluations">
        {agent.competencies.length ? (
          <div className="competency-grid">
            {agent.competencies.map((c) => (
              <Panel key={c.capability} className="competency">
                <Gauge
                  ratio={c.estimated_score}
                  label="estimate"
                  reading={score(c.estimated_score)}
                  tone={c.estimated_score >= 0.8 ? "ok" : c.estimated_score >= 0.5 ? "accent" : "wait"}
                  size={116}
                />
                <div className="competency-name">{c.capability}</div>
                <div className="competency-meta">
                  {`±${score(c.uncertainty)} uncertainty · ${c.sample_count} ${c.sample_count === 1 ? "sample" : "samples"} (${c.effective_samples.toFixed(1)} eff.)`}
                </div>
                <div className="competency-meta">
                  trend {c.recent_trend === null ? "—" : c.recent_trend.toFixed(2)} · calibration {score(c.calibration)}
                </div>
                <div className="competency-meta mono">last {when(c.last_evaluated)}</div>
              </Panel>
            ))}
          </div>
        ) : (
          <Empty>No measured competency yet: routing relies on declared capabilities until evaluations arrive.</Empty>
        )}
      </Section>

      <div className="cols">
        <Section title="Evaluation dimensions">
          {usage.dimensions.length ? (
            <table>
              <tbody>
                {usage.dimensions.map((d) => (
                  <tr key={d.dimension}>
                    <td>{d.dimension.replaceAll("_", " ")}</td>
                    <td>
                      <Meter value={d.mean} />
                    </td>
                    <td className="num">n={d.samples}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : (
            <Empty>No scored evaluations.</Empty>
          )}
        </Section>
        <Section title="Failure clusters">
          {usage.failures_by_kind.length || usage.findings.length ? (
            <table>
              <tbody>
                {usage.failures_by_kind.map((f) => (
                  <tr key={`k-${f.key}`}>
                    <td>
                      <span className="tag violet">execution</span> {f.key}
                    </td>
                    <td className="num">{f.count}</td>
                  </tr>
                ))}
                {usage.findings.map((f) => (
                  <tr key={`f-${f.key}`}>
                    <td>
                      <span className="tag cyan">finding</span> {f.key}
                    </td>
                    <td className="num">{f.count}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : (
            <Empty>No failures recorded.</Empty>
          )}
        </Section>
      </div>

      <Section title="Model usage">
        {usage.models.length ? (
          <table>
            <thead>
              <tr>
                <th>Model</th>
                <th className="num">Calls</th>
                <th className="num">p50 / p95</th>
                <th className="num">Tokens in / out</th>
                <th>Cache hit</th>
                <th className="num">Cost</th>
              </tr>
            </thead>
            <tbody>
              {usage.models.map((m) => (
                <tr key={`${m.provider}/${m.model}`}>
                  <td>
                    <span className="tag violet">
                      {m.provider}/{m.model}
                    </span>
                  </td>
                  <td className="num">{num(m.calls)}</td>
                  <td className="num">
                    {ms(m.latency_p50_ms)} / {ms(m.latency_p95_ms)}
                  </td>
                  <td className="num">
                    {num(m.input_tokens)} / {num(m.output_tokens)}
                  </td>
                  <td>
                    <Meter value={m.cache_hit_rate} tone="ok" />
                  </td>
                  <td className="num">{usd(m.cost_micro_usd)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : (
          <Empty>No model calls.</Empty>
        )}
      </Section>

      <Section title="Configuration" aside="immutable agent version">
        <div className="cols">
          <Panel className="kv">
            <div>
              <div className="kv-label">Tools</div>
              <div className="tags">
                {agent.tools.length
                  ? agent.tools.map((t) => (
                      <span key={t} className="tag cyan">
                        {t}
                      </span>
                    ))
                  : "—"}
              </div>
            </div>
            <div>
              <div className="kv-label">Capabilities</div>
              <div className="tags">
                {agent.capabilities.length
                  ? agent.capabilities.map((c) => (
                      <span key={c} className="tag">
                        {c}
                      </span>
                    ))
                  : "—"}
              </div>
            </div>
            <div>
              <div className="kv-label">Pinned skills</div>
              <div className="tags">
                {agent.skills.length
                  ? agent.skills.map((s) => (
                      <span key={s} className="tag violet">
                        {s}
                      </span>
                    ))
                  : "—"}
              </div>
            </div>
          </Panel>
          <pre className="output">{agent.instructions}</pre>
        </div>
      </Section>
    </>
  );
}
