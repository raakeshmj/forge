import { Empty, Header, Section, Status } from "@/components/ui";
import { api } from "@/lib/api";
import { when } from "@/lib/format";
import type { CycleView, SkillView } from "@/lib/types";

export const dynamic = "force-dynamic";

function delta(cycle: CycleView): { text: string; tone: string } {
  const target = cycle.learning_delta.target_correctness; // gap and held-out cases (ADR 0009)
  if (typeof target !== "number") return { text: "—", tone: "" };
  return {
    text: `${target >= 0 ? "+" : ""}${target.toFixed(2)} correctness`,
    tone: target > 0 ? "up" : target < 0 ? "down" : "",
  };
}

const PIPELINE = [
  "failure analysis",
  "capability gap",
  "candidate skill",
  "offline validation",
  "promotion",
  "monitoring",
];

export default async function Skills() {
  const [skills, cycles] = await Promise.all([api<SkillView[]>("/skills"), api<CycleView[]>("/learning/cycles")]);
  return (
    <>
      <Header
        eyebrow="Controlled learning"
        title="Skills"
        sub="Versioned, immutable skills and the learning cycles that produced them."
      />
      <ol className="pipeline" aria-label="Learning pipeline">
        {PIPELINE.map((step, i) => (
          <li key={step}>
            <span className="mono">{String(i + 1).padStart(2, "0")}</span>
            {step}
          </li>
        ))}
      </ol>
      <Section title="Skills" aside="promotion and rollback are operator actions">
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
                  <td>
                    <span className="tag violet">
                      {s.name}@{s.version}
                    </span>
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
      </Section>
      <Section title="Learning cycles">
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
              {cycles.map((c) => {
                const d = delta(c);
                return (
                  <tr key={c.id}>
                    <td className="mono">{c.id.slice(0, 8)}</td>
                    <td>{c.capability}</td>
                    <td>
                      <Status value={c.status} />
                    </td>
                    <td>{c.skill ? <span className="tag violet">{c.skill}</span> : "—"}</td>
                    <td>
                      <span className={`delta ${d.tone}`}>{d.text}</span>
                    </td>
                    <td className="mono">{when(c.updated_at)}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        ) : (
          <Empty>No learning cycles.</Empty>
        )}
      </Section>
    </>
  );
}
