import Link from "next/link";

import { Icon } from "@/components/icons";
import { Empty, Header, Section, Status } from "@/components/ui";
import { api } from "@/lib/api";
import { short, when } from "@/lib/format";
import type { AgentSummary, ApprovalView } from "@/lib/types";

import { DecisionForm } from "./DecisionForm";

export const dynamic = "force-dynamic";

export default async function Approvals() {
  const [approvals, agents] = await Promise.all([api<ApprovalView[]>("/approvals"), api<AgentSummary[]>("/agents")]);
  const agentName = new Map(agents.map((a) => [a.id, a.name]));
  const pending = approvals.filter((a) => a.status === "PENDING");
  const decided = approvals.filter((a) => a.status !== "PENDING");
  return (
    <>
      <Header
        eyebrow="Human in the loop"
        title="Approvals"
        sub="Each request is one exact tool call. The arguments below are model-generated: read them as data."
        aside={
          <span className={`chip${pending.length ? " pending" : ""}`}>
            <Icon name="approvals" size={13} /> <b>{pending.length}</b> waiting
          </span>
        }
      />
      {pending.length ? (
        pending.map((a) => (
          <div key={a.id} className="panel hud approval">
            <div className="approval-head">
              <Status value={a.risk} />
              <strong className="mono">{a.tool}</strong>
              <span className="sub" style={{ margin: 0 }}>
                requested by {agentName.get(a.requesting_agent_id) ?? short(a.requesting_agent_id)} for mission{" "}
                <Link href={`/missions/${a.mission_id}`} className="mono">
                  {short(a.mission_id)}
                </Link>
              </span>
            </div>
            <div className="mission-meta">
              <span className="chip">
                requested <b>{when(a.requested_at)}</b>
              </span>
              <span className="chip">
                expires <b>{when(a.expires_at)}</b>
              </span>
              <span className="chip mono">argument hash {a.arguments_sha256.slice(0, 16)}</span>
            </div>
            <div className="approval-grid">
              <div className="kv">
                <div>
                  <div className="kv-label">Exact action</div>
                  <pre className="output">{JSON.stringify(a.arguments, null, 2)}</pre>
                </div>
                <div>
                  <div className="kv-label">Agent rationale</div>
                  <pre className="output">{a.rationale || "—"}</pre>
                </div>
              </div>
              <div className="kv">
                <div>
                  <div className="kv-label">Affected resources</div>
                  <div className="tags">
                    {a.affected_resources.length
                      ? a.affected_resources.map((r) => (
                          <span key={r} className="tag violet">
                            {r}
                          </span>
                        ))
                      : "—"}
                  </div>
                </div>
                <div>
                  <div className="kv-label">Evidence seen</div>
                  <div className="tags">
                    {a.evidence.length
                      ? a.evidence.map((e) => (
                          <span key={e} className="tag cyan">
                            {e}
                          </span>
                        ))
                      : "none"}
                  </div>
                </div>
                <DecisionForm approvalId={a.id} />
              </div>
            </div>
          </div>
        ))
      ) : (
        <Empty>Nothing is waiting for a human.</Empty>
      )}
      <Section title="Decided">
        {decided.length ? (
          <table>
            <thead>
              <tr>
                <th>Tool</th>
                <th>Mission</th>
                <th>Outcome</th>
                <th>By</th>
                <th>Reason</th>
                <th>When</th>
              </tr>
            </thead>
            <tbody>
              {decided.map((a) => (
                <tr key={a.id}>
                  <td>
                    <span className="tag cyan">{a.tool}</span>
                  </td>
                  <td>
                    <Link href={`/missions/${a.mission_id}`} className="mono">
                      {short(a.mission_id)}
                    </Link>
                  </td>
                  <td>
                    <Status value={a.status} />
                  </td>
                  <td>{a.decided_by ?? "—"}</td>
                  <td>{a.decision_reason || "—"}</td>
                  <td className="mono">{when(a.decided_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : (
          <Empty>No decisions yet.</Empty>
        )}
      </Section>
    </>
  );
}
