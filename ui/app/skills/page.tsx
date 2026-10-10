import { Empty, Header, Status } from "@/components/ui";
import { api } from "@/lib/api";
import { when } from "@/lib/format";
import type { CycleView, SkillView } from "@/lib/types";

export const dynamic = "force-dynamic";

function delta(cycle: CycleView): string {
  const target = cycle.learning_delta.target_correctness; // gap and held-out cases (ADR 0009)
  return typeof target === "number" ? `${target >= 0 ? "+" : ""}${target.toFixed(2)} correctness` : "—";
}

export default async function Skills() {
  const [skills, cycles] = await Promise.all([api<SkillView[]>("/skills"), api<CycleView[]>("/learning/cycles")]);
  return (
    <>
      <Header title="Skills" sub="Versioned, immutable skills and the learning cycles that produced them." />
      {skills.length ? (
        <table>
          <thead>
            <tr>
              <th>Skill</th>
              <th>Capability</th>
              <th>Status</th>
              <th>Source</th>
              <th>Created</th>
            </tr>
          </thead>
          <tbody>
            {skills.map((s) => (
              <tr key={`${s.name}@${s.version}`}>
                <td className="mono">
                  {s.name}@{s.version}
                </td>
                <td>{s.capability}</td>
                <td>
                  <Status value={s.status} />
                </td>
                <td>{String(s.provenance.generator ?? s.provenance.source ?? "—")}</td>
                <td className="mono">{when(s.created_at)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : (
        <Empty>No skills yet.</Empty>
      )}
      <h2>Learning cycles</h2>
      {cycles.length ? (
        <table>
          <thead>
            <tr>
              <th>Cycle</th>
              <th>Capability</th>
              <th>Status</th>
              <th>Skill</th>
              <th>Learning delta</th>
              <th>Updated</th>
            </tr>
          </thead>
          <tbody>
            {cycles.map((c) => (
              <tr key={c.id}>
                <td className="mono">{c.id.slice(0, 8)}</td>
                <td>{c.capability}</td>
                <td>
                  <Status value={c.status} />
                </td>
                <td className="mono">{c.skill ?? "—"}</td>
                <td>{delta(c)}</td>
                <td className="mono">{when(c.updated_at)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : (
        <Empty>No learning cycles.</Empty>
      )}
    </>
  );
}
