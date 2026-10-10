import { FlowGraph } from "@/components/FlowGraph";
import { Empty, Header } from "@/components/ui";
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
      <Header title="Capability graph" sub="Capabilities, their specialisations and prerequisites, and who is measured on each." />
      {capabilities.length ? <FlowGraph nodes={placed} edges={edges} /> : <Empty>No capabilities.</Empty>}
      <h2>Measured competency by capability</h2>
      <table>
        <thead>
          <tr>
            <th>Capability</th>
            <th>Description</th>
            <th>Agents (estimate ± uncertainty, samples)</th>
          </tr>
        </thead>
        <tbody>
          {capabilities.map((c) => (
            <tr key={c.name}>
              <td>{c.name}</td>
              <td>{c.description || "—"}</td>
              <td>
                {details
                  .flatMap((a) =>
                    a.competencies
                      .filter((k) => k.capability === c.name)
                      .map((k) => `${a.name} ${score(k.estimated_score)} ± ${score(k.uncertainty)} (n=${k.sample_count})`),
                  )
                  .join("; ") || "—"}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </>
  );
}
