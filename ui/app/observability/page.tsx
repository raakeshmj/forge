import Link from "next/link";

import { Distribution, Empty, Header, Meter, Section, Stat } from "@/components/ui";
import { api } from "@/lib/api";
import { ms, num, pct, usd } from "@/lib/format";
import type { Overview } from "@/lib/types";

export const dynamic = "force-dynamic";

const WINDOWS: [string, string][] = [
  ["", "all time"],
  ["24", "24 h"],
  ["168", "7 days"],
];

export default async function Observability({ searchParams }: { searchParams: Promise<{ hours?: string }> }) {
  const { hours } = await searchParams;
  const window = hours && /^\d+$/.test(hours) ? hours : "";
  const o = await api<Overview>(`/insights${window ? `?hours=${window}` : ""}`);
  const executions = o.executions_by_status.reduce((t, c) => t + c.count, 0);
  const failed = o.executions_by_status.find((c) => c.key === "FAILED")?.count ?? 0;
  const read = o.models.reduce((t, m) => t + m.cache_read_input_tokens, 0);
  const prompt = o.models.reduce(
    (t, m) => t + m.input_tokens + m.cache_read_input_tokens + m.cache_creation_input_tokens,
    0,
  );
  const savings = o.models.reduce((t, m) => t + m.cache_savings_micro_usd, 0);
  return (
    <>
      <Header
        eyebrow="Measure"
        title="Observability"
        sub="Latency, tokens, caching, tools, and failures, from the system of record."
      />
      <nav className="filters" aria-label="Window">
        {WINDOWS.map(([h, label]) => (
          <Link key={label} href={h ? `?hours=${h}` : "?"} className={h === window ? "on" : undefined}>
            {label}
          </Link>
        ))}
      </nav>
      <div className="stats">
        <Stat label="Executions" value={num(executions)} icon="layers" />
        <Stat
          label="Execution failure rate"
          value={pct(executions ? failed / executions : 0)}
          icon="policies"
          tone={failed ? "bad" : "ok"}
        />
        <Stat label="Prompt tokens" value={num(prompt)} icon="coin" />
        <Stat label="Cached tokens" value={num(read)} icon="spark" />
        <Stat label="Cache hit rate" value={pct(prompt ? read / prompt : 0)} icon="bolt" tone="accent" />
        <Stat label="Cache savings" value={usd(savings)} icon="coin" tone={savings > 0 ? "ok" : undefined} />
      </div>
      <Section title="Models" aside="latency percentiles p50 / p95 / p99">
        {o.models.length ? (
          <table>
            <thead>
              <tr>
                <th>Model</th>
                <th className="num">Calls</th>
                <th className="num">p50</th>
                <th className="num">p95</th>
                <th className="num">p99</th>
                <th className="num">In</th>
                <th className="num">Out</th>
                <th className="num">Cache read</th>
                <th className="num">Cache write</th>
                <th>Hit rate</th>
              </tr>
            </thead>
            <tbody>
              {o.models.map((m) => (
                <tr key={`${m.provider}/${m.model}`}>
                  <td>
                    <span className="tag violet">
                      {m.provider}/{m.model}
                    </span>
                  </td>
                  <td className="num">{num(m.calls)}</td>
                  <td className="num">{ms(m.latency_p50_ms)}</td>
                  <td className="num">{ms(m.latency_p95_ms)}</td>
                  <td className="num">{ms(m.latency_p99_ms)}</td>
                  <td className="num">{num(m.input_tokens)}</td>
                  <td className="num">{num(m.output_tokens)}</td>
                  <td className="num">{num(m.cache_read_input_tokens)}</td>
                  <td className="num">{num(m.cache_creation_input_tokens)}</td>
                  <td>
                    <Meter value={m.cache_hit_rate} tone="ok" />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : (
          <Empty>No model calls in this window.</Empty>
        )}
      </Section>
      <Section title="Tools" aside="outcomes through the security pipeline">
        {o.tools.length ? (
          <table>
            <thead>
              <tr>
                <th>Tool</th>
                <th className="num">Calls</th>
                <th className="num">Error results</th>
                <th className="num">Failures</th>
                <th className="num">Denied</th>
                <th className="num">Re-executed after crash</th>
              </tr>
            </thead>
            <tbody>
              {o.tools.map((t) => (
                <tr key={t.tool}>
                  <td>
                    <span className="tag cyan">{t.tool}</span>
                  </td>
                  <td className="num">{num(t.calls)}</td>
                  <td className="num">{num(t.errors)}</td>
                  <td className="num">{num(t.failures)}</td>
                  <td className="num">{num(t.denials)}</td>
                  <td className="num">{num(t.reinvocations)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : (
          <Empty>No tool calls in this window.</Empty>
        )}
      </Section>
      <div className="cols">
        <Section title="Execution outcomes">
          <div className="panel hud">
            <Distribution rows={o.executions_by_status} />
          </div>
          <table style={{ marginTop: 12 }}>
            <tbody>
              {o.executions_by_status.map((c) => (
                <tr key={c.key}>
                  <td>{c.key.toLowerCase()}</td>
                  <td className="num">{num(c.count)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Section>
        <Section title="Failures by kind">
          {o.failures_by_kind.length ? (
            <table>
              <tbody>
                {o.failures_by_kind.map((c) => (
                  <tr key={c.key}>
                    <td>
                      <span className="tag violet">{c.key}</span>
                    </td>
                    <td className="num">{num(c.count)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : (
            <Empty>No failures.</Empty>
          )}
        </Section>
      </div>
    </>
  );
}
