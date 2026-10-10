import Link from "next/link";

import { MissionTable } from "@/components/MissionTable";
import { Empty, Header } from "@/components/ui";
import { api } from "@/lib/api";
import type { MissionSummary } from "@/lib/types";

export const dynamic = "force-dynamic";

const STATUSES = ["RUNNING", "WAITING_APPROVAL", "COMPLETED", "FAILED", "CANCELLED"];

export default async function Missions({ searchParams }: { searchParams: Promise<{ status?: string }> }) {
  const { status } = await searchParams;
  const query = status && STATUSES.includes(status) ? `&status=${status}` : "";
  const missions = await api<MissionSummary[]>(`/missions?limit=200${query}`);
  return (
    <>
      <Header title="Missions" sub="Newest first, with progress, tokens, and spend." />
      <p>
        <Link href="/missions">all</Link>
        {STATUSES.map((s) => (
          <span key={s}>
            {" · "}
            <Link href={`/missions?status=${s}`}>{s.toLowerCase().replace("_", " ")}</Link>
          </span>
        ))}
      </p>
      {missions.length ? <MissionTable missions={missions} /> : <Empty>No missions match.</Empty>}
    </>
  );
}
