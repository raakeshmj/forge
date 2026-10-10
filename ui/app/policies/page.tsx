import { Empty, Header } from "@/components/ui";
import { api } from "@/lib/api";
import { when } from "@/lib/format";
import type { AuditView, PolicyView, ToolView } from "@/lib/types";

export const dynamic = "force-dynamic";

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
        title="Policies"
        sub="LOW runs · MEDIUM needs an allowing rule · HIGH needs a human · CRITICAL is off unless enabled (ADR 0010)."
      />
      {policies.length ? (
        policies.map((p) => (
          <div key={p.name} style={{ marginBottom: 14 }}>
            <h2>
              {p.name} v{p.version}
            </h2>
            <p className="sub">
              written by {p.created_by} · {when(p.created_at)} · first matching rule wins
            </p>
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
                    <td>{r.effect}</td>
                    <td className="mono">{r.tools.join(", ")}</td>
                    <td>{r.agents.join(", ") || "any"}</td>
                    <td className="mono">
                      {r.conditions.map((c) => `${c.argument} ${c.operator} ${JSON.stringify(c.value)}`).join(" and ") || "—"}
                    </td>
                    <td>{r.reason || "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ))
      ) : (
        <Empty>No policies: every MEDIUM tool is denied (fail closed).</Empty>
      )}
      <h2>Tools</h2>
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
              <td className="mono">{t.name}</td>
              <td className="mono">{t.pinned_version}</td>
              <td>{t.status.toLowerCase()}{t.reason ? `: ${t.reason}` : ""}</td>
              <td>{critical.includes(t.name) ? "yes" : "—"}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <h2>Audit log</h2>
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
                  {a.actor} <span className="sub">({a.actor_type})</span>
                </td>
                <td>{a.action}</td>
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
    </>
  );
}
