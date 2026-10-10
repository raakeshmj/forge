import Link from "next/link";

import { MissionTable } from "@/components/MissionTable";
import { Empty, Header, Stat } from "@/components/ui";
import { api } from "@/lib/api";
import { ms, num, pct, usd } from "@/lib/format";
import type { MissionSummary, Overview } from "@/lib/types";

export const dynamic = "force-dynamic";

export default async function Dashboard() {
  const [overview, missions] = await Promise.all([
    api<Overview>("/insights"),
    api<MissionSummary[]>("/missions?limit=10"),
  ]);
  const count = (key: string) => overview.missions_by_status.find((c) => c.key === key)?.count ?? 0;
  const calls = overview.models.reduce((t, m) => t + m.calls, 0);
  const read = overview.models.reduce((t, m) => t + m.cache_read_input_tokens, 0);
  const prompt = overview.models.reduce(
    (t, m) => t + m.input_tokens + m.cache_read_input_tokens + m.cache_creation_input_tokens,
    0,
  );
  const slowest = Math.max(0, ...overview.models.map((m) => m.latency_p95_ms));
  return (
    <>
      <Header title="Dashboard" sub="Everything FORGE has run, from the system of record." />
      <div className="stats">
        <Stat label="Running" value={count("RUNNING") + count("PLANNING") + count("READY")} />
        <Stat label="Awaiting approval" value={<Link href="/approvals">{overview.pending_approvals}</Link>} />
        <Stat label="Completed" value={count("COMPLETED")} />
        <Stat label="Failed" value={count("FAILED")} />
        <Stat label="Model calls" value={num(calls)} />
        <Stat label="Cache hit rate" value={pct(prompt ? read / prompt : 0)} />
        <Stat label="Model p95 latency" value={ms(slowest)} />
        <Stat label="Total spend" value={usd(overview.total_cost_micro_usd)} />
      </div>
      <h2>Recent missions</h2>
      {missions.length === 0 ? (
        <Empty>No missions yet. Submit one with POST /missions.</Empty>
      ) : (
        <MissionTable missions={missions} />
      )}
    </>
  );
}
