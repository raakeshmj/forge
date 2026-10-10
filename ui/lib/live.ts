// Which streamed events change what the mission page shows, so the page re-renders on them.

const MILESTONES = new Set([
  "mission.planned",
  "mission.completed",
  "mission.failed",
  "mission.cancelled",
  "task.completed",
  "task.failed",
  "task.cancelled",
  "approval.requested",
  "approval.completed",
  "evaluation.completed",
]);

/** True when an event changes the page's server-rendered state: status, tasks, results, scores. */
export function isMilestone(eventType: string): boolean {
  return MILESTONES.has(eventType);
}
