import { describe, expect, it } from "vitest";

import { isMilestone } from "./live";

describe("isMilestone", () => {
  it("re-renders on state changes the page shows", () => {
    for (const type of ["mission.planned", "task.completed", "mission.completed", "evaluation.completed"]) {
      expect(isMilestone(type)).toBe(true);
    }
  });

  it("does not re-render on every model call or checkpoint", () => {
    for (const type of ["model.called", "checkpoint.created", "tool.called", "agent.started"]) {
      expect(isMilestone(type)).toBe(false);
    }
  });
});
