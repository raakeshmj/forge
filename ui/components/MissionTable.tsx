import Link from "next/link";

import { Status } from "@/components/ui";
import { num, short, usd, when } from "@/lib/format";
import type { MissionSummary } from "@/lib/types";

export function MissionTable({ missions }: { missions: MissionSummary[] }) {
  return (
    <table>
      <thead>
        <tr>
          <th>Mission</th>
          <th>Status</th>
          <th className="num">Tasks</th>
          <th className="num">Tokens</th>
          <th className="num">Cost</th>
          <th>Created</th>
        </tr>
      </thead>
      <tbody>
        {missions.map((m) => (
          <tr key={m.id}>
            <td>
              <Link href={`/missions/${m.id}`}>{m.objective.slice(0, 90)}</Link>
              <span className="id-chip">{short(m.id)}</span>
            </td>
            <td>
              <Status value={m.status} />
            </td>
            <td className="num">
              {m.tasks_completed}/{m.tasks_total}
            </td>
            <td className="num">{num(m.input_tokens + m.output_tokens + m.cache_read_input_tokens)}</td>
            <td className="num">{usd(m.cost_micro_usd)}</td>
            <td className="mono">{when(m.created_at)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
