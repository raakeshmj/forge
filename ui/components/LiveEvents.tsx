"use client";

import { useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";

import { isMilestone } from "@/lib/live";

interface Item {
  id: string;
  type: string;
  at: string;
  fresh?: boolean;
}

/** Colour class for an event type: model, tool, evaluation, approval, checkpoint, failure. */
function tone(type: string): string {
  if (type.endsWith(".failed") || type.endsWith(".denied")) return "ev-fail";
  const family = type.split(".")[0];
  return family && ["model", "tool", "evaluation", "approval", "checkpoint"].includes(family) ? `ev-${family}` : "";
}

/**
 * Streams a mission's events through the UI server's read-only proxy (ADR 0012), and
 * re-renders the server-rendered page on milestones so status, tasks, and scores stay live.
 */
export function LiveEvents({ missionId, initial }: { missionId: string; initial: Item[] }) {
  const router = useRouter();
  const [items, setItems] = useState<Item[]>(initial);
  const [state, setState] = useState<"connecting" | "live" | "ended">("connecting");
  // What the page was rendered with: read once, so a refresh never restarts the stream.
  const rendered = useRef(initial);

  useEffect(() => {
    const source = new EventSource(`/api/forge/missions/${missionId}/events/stream`);
    const seen = new Set(rendered.current.map((item) => item.id));
    let refresh: ReturnType<typeof setTimeout> | undefined;
    const scheduleRefresh = () => {
      clearTimeout(refresh);
      refresh = setTimeout(() => router.refresh(), 300); // coalesce bursts of milestones
    };
    source.onopen = () => setState("live");
    source.onmessage = (message: MessageEvent<string>) => {
      if (!message.lastEventId || seen.has(message.lastEventId)) return;
      seen.add(message.lastEventId);
      const data = JSON.parse(message.data) as { event_type: string; occurred_at: string };
      setItems((current) => [
        ...current,
        { id: message.lastEventId, type: data.event_type, at: data.occurred_at, fresh: true },
      ]);
      if (isMilestone(data.event_type)) scheduleRefresh();
    };
    source.addEventListener("end", () => {
      setState("ended");
      source.close();
    });
    return () => {
      clearTimeout(refresh);
      source.close();
    };
  }, [missionId, router]);

  return (
    <div className="panel terminal hud">
      <div className="terminal-bar">
        <span className="lights">
          <i />
          <i />
          <i />
        </span>
        <span>telemetry://missions/{missionId.slice(0, 8)}/events</span>
        <span className={`feed-state ${state}`}>
          {state === "live" ? <i /> : null}
          {state === "live" ? "live" : state === "ended" ? "mission ended: history complete" : "connecting…"}
          {` · ${items.length} events`}
        </span>
      </div>
      <div className="feed">
        <ul>
          {[...items].reverse().map((item, index) => (
            <li key={item.id} className={item.fresh ? "fresh" : undefined}>
              <span className="t">{item.at.slice(11, 23)}</span>
              <span className={`${tone(item.type)}${index === 0 && state === "live" ? " cursor" : ""}`}>
                {item.type}
              </span>
            </li>
          ))}
        </ul>
      </div>
    </div>
  );
}
