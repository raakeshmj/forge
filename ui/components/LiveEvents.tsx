"use client";

import { useEffect, useState } from "react";

interface Item {
  id: string;
  type: string;
  at: string;
}

/** Streams a mission's events through the UI server's read-only proxy (ADR 0012). */
export function LiveEvents({ missionId, initial }: { missionId: string; initial: Item[] }) {
  const [items, setItems] = useState<Item[]>(initial);
  const [state, setState] = useState<"connecting" | "live" | "ended">("connecting");

  useEffect(() => {
    const source = new EventSource(`/api/forge/missions/${missionId}/events/stream`);
    const seen = new Set(initial.map((item) => item.id));
    source.onopen = () => setState("live");
    source.onmessage = (message: MessageEvent<string>) => {
      if (!message.lastEventId || seen.has(message.lastEventId)) return;
      seen.add(message.lastEventId);
      const data = JSON.parse(message.data) as { event_type: string; occurred_at: string };
      setItems((current) => [...current, { id: message.lastEventId, type: data.event_type, at: data.occurred_at }]);
    };
    source.addEventListener("end", () => {
      setState("ended");
      source.close();
    });
    return () => source.close();
  }, [missionId, initial]);

  return (
    <div className="panel">
      <div className={state === "live" ? "live" : "sub"}>
        {state === "live" ? "● live" : state === "ended" ? "mission ended: history complete" : "connecting…"}
        {` · ${items.length} events`}
      </div>
      <div className="feed">
        <ul>
          {[...items].reverse().map((item) => (
            <li key={item.id}>
              {item.at.slice(11, 23)} {item.type}
            </li>
          ))}
        </ul>
      </div>
    </div>
  );
}
