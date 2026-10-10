// Shared by the New mission form and its server action.

/** A guard against typos, not a policy: the API and runtime enforce the budget itself. */
export const MAX_BUDGET_USD = 10;

/** Risk ceilings a mission may be launched with. CRITICAL tools are off unless an operator enables them. */
export const RISKS = ["LOW", "MEDIUM", "HIGH"] as const;

/** One-click objectives: the incident scenarios the demo tools hold data for. */
export const EXAMPLES = [
  "Why did checkout latency increase after yesterday's deployment?",
  "Investigate whether payments latency changed after yesterday's deployment.",
  "Explain the inventory latency spike this morning.",
];
