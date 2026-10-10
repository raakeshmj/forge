import Link from "next/link";

import { Empty, Header } from "@/components/ui";
import { api } from "@/lib/api";
import { score, short, when } from "@/lib/format";
import type { EvaluationView } from "@/lib/types";

export const dynamic = "force-dynamic";

const DIMENSIONS = [
  "correctness",
  "evidence_quality",
  "trajectory_quality",
  "tool_selection",
  "efficiency",
  "calibration",
  "safety",
  "cost_efficiency",
];

export default async function Evaluations() {
  const evaluations = await api<EvaluationView[]>("/evaluations?limit=100");
  return (
    <>
      <Header title="Evaluations" sub="Mission-level scores on every dimension; n/a means no evidence to score it." />
      {evaluations.length ? (
        <table>
          <thead>
            <tr>
              <th>Mission</th>
              <th>When</th>
              <th>Ground truth</th>
              {DIMENSIONS.map((d) => (
                <th key={d} className="num">
                  {d.replace("_quality", "").replace("_", " ")}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {evaluations.map((e) => {
              const byDimension = new Map(
                e.results.filter((r) => r.scope === "mission").map((r) => [r.dimension, r.score]),
              );
              return (
                <tr key={e.id}>
                  <td>
                    <Link href={`/missions/${e.mission_id}`} className="mono">
                      {short(e.mission_id)}
                    </Link>
                  </td>
                  <td className="mono">{when(e.created_at)}</td>
                  <td className="mono">{e.benchmark_case ?? "—"}</td>
                  {DIMENSIONS.map((d) => (
                    <td key={d} className="num">
                      {score(byDimension.get(d))}
                    </td>
                  ))}
                </tr>
              );
            })}
          </tbody>
        </table>
      ) : (
        <Empty>No evaluations yet.</Empty>
      )}
    </>
  );
}
