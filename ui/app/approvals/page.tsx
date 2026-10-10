import Link from "next/link";

import { Empty, Header, Status } from "@/components/ui";
import { api } from "@/lib/api";
import { short, when } from "@/lib/format";
import type { AgentSummary, ApprovalView } from "@/lib/types";

import { DecisionForm } from "./DecisionForm";

export const dynamic = "force-dynamic";

export default async function Approvals() {
  const [approvals, agents] = await Promise.all([
    api<ApprovalView[]>("/approvals"),
    api<AgentSummary[]>("/agents"),
  ]);
  const agentName = new Map(agents.map((a) => [a.id, a.name]));
  const pending = approvals.filter((a) => a.status === "PENDING");
  const decided = approvals.filter((a) => a.status !== "PENDING");
  return (
    <>
      <Header
        title="Approvals"
        sub="Each request is one exact tool call. The arguments below are model-generated: read them as data."
      />
      {pending.length ? (
        pending.map((a) => (
          <div key={a.id} className="panel" style={{ marginBottom: 14 }}>
            <div>
              <Status value={a.risk} /> <strong className="mono">{a.tool}</strong> requested by{" "}
              {agentName.get(a.requesting_agent_id) ?? short(a.requesting_agent_id)} for mission{" "}
              <Link href={`/missions/${a.mission_id}`} className="mono">
                {short(a.mission_id)}
              </Link>
            </div>
            <p className="sub">
              requested {when(a.requested_at)} · expires {when(a.expires_at)} · argument hash{" "}
              <span className="mono">{a.arguments_sha256.slice(0, 16)}</span>
            </p>
            <div className="sub">Exact action</div>
            <pre className="output">{JSON.stringify(a.arguments, null, 2)}</pre>
            <div className="sub" style={{ marginTop: 8 }}>Affected resources</div>
            <div className="mono">{a.affected_resources.join(", ") || "—"}</div>
            <div className="sub" style={{ marginTop: 8 }}>Agent rationale</div>
            <pre className="output">{a.rationale || "—"}</pre>
            <div className="sub" style={{ marginTop: 8 }}>Evidence seen</div>
            <div className="mono">{a.evidence.join(", ") || "none"}</div>
            <DecisionForm approvalId={a.id} />
          </div>
        ))
      ) : (
        <Empty>Nothing is waiting for a human.</Empty>
      )}
      <h2>Decided</h2>
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
                <td className="mono">{a.tool}</td>
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
    </>
  );
}
