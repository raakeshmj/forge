import Link from "next/link";

import { Empty, Header } from "@/components/ui";
import { api } from "@/lib/api";
import { when } from "@/lib/format";
import type { AgentSummary } from "@/lib/types";

export const dynamic = "force-dynamic";

export default async function Agents() {
  const agents = await api<AgentSummary[]>("/agents");
  return (
    <>
      <Header title="Agents" sub="Current version of every agent: its role, model, tools, capabilities, and pinned skills." />
      {agents.length === 0 ? (
        <Empty>No agents registered.</Empty>
      ) : (
        <table>
          <thead>
            <tr>
              <th>Agent</th>
              <th className="num">Version</th>
              <th>Role</th>
              <th>Model</th>
              <th>Capabilities</th>
              <th>Skills</th>
              <th>Since</th>
            </tr>
          </thead>
          <tbody>
            {agents.map((a) => (
              <tr key={a.id}>
                <td>
                  <Link href={`/agents/${a.id}`}>{a.name}</Link>
                </td>
                <td className="num">v{a.version}</td>
                <td>{a.role}</td>
                <td className="mono">{a.model}</td>
                <td>{a.capabilities.join(", ") || "—"}</td>
                <td className="mono">{a.skills.join(", ") || "—"}</td>
                <td className="mono">{when(a.created_at)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </>
  );
}
