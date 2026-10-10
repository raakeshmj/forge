import Link from "next/link";

import { Icon } from "@/components/icons";
import { MissionTable } from "@/components/MissionTable";
import { Empty, Header } from "@/components/ui";
import { api } from "@/lib/api";
import type { MissionSummary } from "@/lib/types";

export const dynamic = "force-dynamic";

const STATUSES = ["RUNNING", "WAITING_APPROVAL", "COMPLETED", "FAILED", "CANCELLED"];

export default async function Missions({ searchParams }: { searchParams: Promise<{ status?: string }> }) {
  const { status } = await searchParams;
  const current = status && STATUSES.includes(status) ? status : undefined;
  const missions = await api<MissionSummary[]>(`/missions?limit=200${current ? `&status=${current}` : ""}`);
  return (
    <>
      <Header
        eyebrow="Operate"
        title="Missions"
        sub="Newest first, with progress, tokens, and spend."
        aside={
          <Link href="/missions/new" className="button">
            <Icon name="launch" size={15} /> New mission
          </Link>
        }
      />
      <nav className="filters" aria-label="Filter by status">
        <Link href="/missions" className={current ? undefined : "on"}>
          all
        </Link>
        {STATUSES.map((s) => (
          <Link key={s} href={`/missions?status=${s}`} className={current === s ? "on" : undefined}>
            {s.toLowerCase().replace("_", " ")}
          </Link>
        ))}
      </nav>
      {missions.length ? <MissionTable missions={missions} /> : <Empty>No missions match.</Empty>}
    </>
  );
}
