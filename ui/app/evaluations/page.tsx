import Link from "next/link";
import type { CSSProperties } from "react";

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

/** Heatmap tint for a score: green when good, amber when middling, red when poor. */
function heat(value: number | null | undefined): CSSProperties | undefined {
  if (value === null || value === undefined) return undefined;
  const colour = value >= 0.8 ? "var(--ok)" : value >= 0.5 ? "var(--warn)" : "var(--bad)";
  return { ["--c" as string]: colour, ["--mix" as string]: `${Math.round(10 + value * 14)}%` };
}

export default async function Evaluations() {
  const evaluations = await api<EvaluationView[]>("/evaluations?limit=100");
  return (
    <>
      <Header
        eyebrow="Measure"
        title="Evaluations"
        sub="Mission-level scores on every dimension; n/a means no evidence to score it."
      />
      {evaluations.length ? (
        <div className="scroll-x">
          <table className="heatmap">
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
                    <td className="mono" title={when(e.created_at)}>
                      {when(e.created_at).slice(5, 16)}
                    </td>
                    <td>
                      {e.benchmark_case ? (
                        <span className="tag cyan truncate" title={e.benchmark_case}>
                          {e.benchmark_case.split("/").slice(1).join("/") || e.benchmark_case}
                        </span>
                      ) : (
                        "—"
                      )}
                    </td>
                    {DIMENSIONS.map((d) => (
                      <td key={d} className="num">
                        <span className="heat" style={heat(byDimension.get(d))}>
                          {score(byDimension.get(d))}
                        </span>
                      </td>
                    ))}
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      ) : (
        <Empty>No evaluations yet.</Empty>
      )}
    </>
  );
}
