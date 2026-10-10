import Link from "next/link";

import { Icon } from "@/components/icons";
import { Empty, Header } from "@/components/ui";
import { api } from "@/lib/api";
import { when } from "@/lib/format";
import type { AgentSummary } from "@/lib/types";

export const dynamic = "force-dynamic";

export default async function Agents() {
  const agents = await api<AgentSummary[]>("/agents");
  return (
    <>
      <Header
        eyebrow="Agents"
        title="Agents"
        sub="Current version of every agent: its role, model, tools, capabilities, and pinned skills."
      />
      {agents.length === 0 ? (
        <Empty>No agents registered.</Empty>
      ) : (
        <div className="cards">
          {agents.map((a) => (
            <Link key={a.id} href={`/agents/${a.id}`} className="card hud agent-card">
              <div className="card-title">
                <span className="agent-name">
                  <Icon name="agents" />
                  {a.name}
                </span>
                <span className="id-chip">v{a.version}</span>
              </div>
              <div className="card-body">{a.role}</div>
              <div className="kv" style={{ marginTop: 14 }}>
                <div>
                  <div className="kv-label">Model</div>
                  <span className="tag violet">{a.model}</span>
                </div>
                <div>
                  <div className="kv-label">Capabilities</div>
                  <div className="tags">
                    {a.capabilities.length
                      ? a.capabilities.map((c) => (
                          <span key={c} className="tag cyan">
                            {c}
                          </span>
                        ))
                      : "—"}
                  </div>
                </div>
                <div>
                  <div className="kv-label">Skills</div>
                  <div className="tags">
                    {a.skills.length
                      ? a.skills.map((s) => (
                          <span key={s} className="tag">
                            {s}
                          </span>
                        ))
                      : "—"}
                  </div>
                </div>
              </div>
              <div className="card-foot mono">since {when(a.created_at)}</div>
            </Link>
          ))}
        </div>
      )}
    </>
  );
}
