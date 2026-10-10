import Link from "next/link";

import { FlowGraph } from "@/components/FlowGraph";
import { Empty, Header, Meter, Section } from "@/components/ui";
import { api } from "@/lib/api";
import { score } from "@/lib/format";
import { layout, type GraphEdge } from "@/lib/graph";
import type { AgentDetail, AgentSummary, Capability } from "@/lib/types";

export const dynamic = "force-dynamic";

export default async function Capabilities() {
  const [capabilities, agents] = await Promise.all([
    api<Capability[]>("/capabilities"),
    api<AgentSummary[]>("/agents"),
  ]);
  const details = await Promise.all(agents.map((a) => api<AgentDetail>(`/agents/${a.id}`)));
  const edges: GraphEdge[] = capabilities.flatMap((c) => [
    ...(c.parent ? [{ from: c.parent, to: c.name, kind: "specialises" }] : []),
    ...c.prerequisites.map((p) => ({ from: p, to: c.name, kind: "prerequisite" })),
  ]);
  const placed = layout(
    capabilities.map((c) => ({ id: c.name, label: c.name })),
    edges,
  );
  return (
    <>
      <Header
        eyebrow="Agents"
        title="Capability graph"
        sub="Capabilities, their specialisations and prerequisites, and who is measured on each."
      />
      {capabilities.length ? <FlowGraph nodes={placed} edges={edges} /> : <Empty>No capabilities.</Empty>}
      <Section title="Measured competency by capability" aside="estimate ± uncertainty, samples">
        <table>
          <thead>
            <tr>
              <th>Capability</th>
              <th>Description</th>
              <th>Agents</th>
            </tr>
          </thead>
          <tbody>
            {capabilities.map((c) => {
              const measured = details.flatMap((a) =>
                a.competencies.filter((k) => k.capability === c.name).map((k) => ({ agent: a, k })),
              );
              return (
                <tr key={c.name}>
                  <td>
                    <span className="tag cyan">{c.name}</span>
                  </td>
                  <td>{c.description || "—"}</td>
                  <td>
                    {measured.length ? (
                      <div className="kv" style={{ gap: 6 }}>
                        {measured.map(({ agent, k }) => (
                          <div key={agent.id} className="competency-row">
                            <Link href={`/agents/${agent.id}`}>{agent.name}</Link>
                            <Meter value={k.estimated_score} />
                            <span className="sub">
                              ± {score(k.uncertainty)} (n={k.sample_count})
                            </span>
                          </div>
                        ))}
                      </div>
                    ) : (
                      "—"
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </Section>
    </>
  );
}
