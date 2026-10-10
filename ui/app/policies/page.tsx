import { Empty, Header, Section, Status } from "@/components/ui";
import { api } from "@/lib/api";
import { when } from "@/lib/format";
import type { AuditView, PolicyView, ToolView } from "@/lib/types";

export const dynamic = "force-dynamic";

const LADDER: [string, string, string][] = [
  ["low", "Runs", "Read-only and reversible calls execute directly."],
  ["medium", "Needs an allowing rule", "Denied unless a policy rule allows it (fail closed)."],
  ["high", "Needs a human", "Waits for an operator to approve the exact call."],
  ["critical", "Off unless enabled", "An operator must enable the tool, then approve each call."],
];

export default async function Policies() {
  const [policies, critical, tools, audit] = await Promise.all([
    api<PolicyView[]>("/policies"),
    api<string[]>("/critical-tools"),
    api<ToolView[]>("/tools"),
    api<AuditView[]>("/audit?limit=100"),
  ]);
  return (
    <>
      <Header
        eyebrow="Govern"
        title="Policies"
        sub="LOW runs · MEDIUM needs an allowing rule · HIGH needs a human · CRITICAL is off unless enabled (ADR 0010)."
      />
      <div className="ladder">
        {LADDER.map(([level, rule, why]) => (
          <div key={level} className={`rung ${level}`}>
            <div className="level">{level.toUpperCase()}</div>
            <div className="rule">{rule}</div>
            <div className="why">{why}</div>
          </div>
        ))}
      </div>
      {policies.length ? (
        policies.map((p) => (
          <Section
            key={p.name}
            title={`${p.name} v${p.version}`}
            aside={`written by ${p.created_by} · ${when(p.created_at)} · first matching rule wins`}
          >
            <table>
              <thead>
                <tr>
                  <th>Effect</th>
                  <th>Tools</th>
                  <th>Agents</th>
                  <th>Conditions</th>
                  <th>Reason</th>
                </tr>
              </thead>
              <tbody>
                {p.rules.map((r, i) => (
                  <tr key={i}>
                    <td>
                      <Status value={r.effect.toLowerCase() === "allow" ? "APPROVED" : "DENIED"} />
                    </td>
                    <td className="mono">{r.tools.join(", ")}</td>
                    <td>{r.agents.join(", ") || "any"}</td>
                    <td className="mono">
                      {r.conditions
                        .map((c) => `${c.argument} ${c.operator} ${JSON.stringify(c.value)}`)
                        .join(" and ") || "—"}
                    </td>
                    <td>{r.reason || "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </Section>
        ))
      ) : (
        <Section title="Policy">
          <Empty>No policies: every MEDIUM tool is denied (fail closed).</Empty>
        </Section>
      )}
      <Section title="Tools" aside="pinned versions; a changed tool is quarantined until re-pinned">
        <table>
          <thead>
            <tr>
              <th>Tool</th>
              <th>Pinned version</th>
              <th>Status</th>
              <th>CRITICAL enabled</th>
            </tr>
          </thead>
          <tbody>
            {tools.map((t) => (
              <tr key={t.name}>
                <td>
                  <span className="tag cyan">{t.name}</span>
                </td>
                <td className="mono">{t.pinned_version}</td>
                <td>
                  <Status value={t.status} />
                  {t.reason ? <span className="sub"> {t.reason}</span> : null}
                </td>
                <td>{critical.includes(t.name) ? <Status value="CRITICAL" /> : "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </Section>
      <Section title="Audit log" aside="append-only">
        {audit.length ? (
          <table>
            <thead>
              <tr>
                <th>When</th>
                <th>Actor</th>
                <th>Action</th>
                <th>Decision</th>
                <th>Tool / target</th>
                <th>Reason</th>
              </tr>
            </thead>
            <tbody>
              {audit.map((a, i) => (
                <tr key={i}>
                  <td className="mono">{when(a.occurred_at)}</td>
                  <td>
                    {a.actor} <span className="id-chip">{a.actor_type}</span>
                  </td>
                  <td className="mono">{a.action}</td>
                  <td>{a.decision}</td>
                  <td className="mono">{a.tool ?? a.target ?? "—"}</td>
                  <td>{a.reason || "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : (
          <Empty>No audit records.</Empty>
        )}
      </Section>
    </>
  );
}
