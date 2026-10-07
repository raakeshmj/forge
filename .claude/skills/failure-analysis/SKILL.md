---
name: failure-analysis
description: Find the root cause of a FORGE system failure (a failing test, a mission that failed, hung, or duplicated work, wrong cost/token/cache numbers, a recovery that didn't recover, a flaky concurrency test), and run a pre-mortem on changes to the execution runtime, state machines, persistence, retries, or side-effecting tools. Applies CLAUDE.md §29 to FORGE. Not for judging the quality of an agent's answer; that is evaluation.
---

# FORGE failure analysis

Pick the mode. **A**: something already went wrong. **B**: a change to durable or side-effecting code is about to be finished.

## Mode A: observed failure

1. **Reconstruct from records instead of guessing.** For the mission_id / correlation_id, gather:
   - **Events,** ordered by aggregate sequence. These are authoritative.
   - **Checkpoints:** sequence and state_version.
   - **Execution rows:** attempts, status, lease holder.
   - **OTel spans.** These are derived. When they disagree with Postgres, Postgres wins, and the disagreement is a separate bug.
   - **Error fields** (CLAUDE §24). A missing field is a finding in its own right.

   Before the inspection tooling exists, rebuild the same sequence in a test fixture.
2. **Find the first divergence.** Look for the earliest event or state where actual differs from expected. That is usually well before the point where the exception surfaced.
3. **Name the violated invariant** from `.claude/skills/architecture-review/invariants.md`. If the behavior is wrong but nothing in the catalog was violated, the invariant is missing. Propose it through `architecture-review`.
4. **Write the failing test first, at the root cause's layer:**

   | Root cause | Test |
   |---|---|
   | State machine or transition logic | Unit test |
   | Transactions, concurrency, leases, duplicate events | Integration test against real Postgres with ≥2 workers. No mocks of the database |
   | Crash or resume | Fault injection at the exact step (kill after side effect, before record) |
   | Provider or MCP behavior | Contract test with recorded fixtures |

5. **Fix the cause.** None of these counts as a fix unless the analysis shows it really is the root cause:
   - adding a retry
   - widening a timeout
   - catching the exception
   - adding a sleep
   - deduplicating downstream of the real source of the duplicate

## Mode B: pre-mortem

List every step that crosses a durability boundary: a DB write, model call, tool call, event emission, or lease/queue operation. For each step, answer every row:

| Fault | Must be true |
|---|---|
| Crash before the step | Resume runs it once |
| Crash after the step, before it is recorded | Re-running is safe because idempotency is declared and a key is used. For model calls, the response was persisted before use (re-calling gives different output) |
| Two workers hold the same execution, or an event is delivered twice | A lease or optimistic version check prevents the second effect |
| Stale read (state changed between read and write) | The write is conditional on the version it read |
| Fan-out partially done, one branch failed | Mission and task states are defined, and finished siblings are not redone |
| Cancel during a side effect | The in-flight effect's outcome is recorded and audited. Nothing new starts |
| Timeout while the remote operation actually succeeded | The outcome is treated as unknown and reconciled, never assumed failed and retried |
| Concurrent tasks share a budget | No overspend, because spend is reserved, not just checked |
| Restart while waiting for approval or pause | The wait survives, and expiry is still enforced |
| Telemetry export fails | Execution continues. Telemetry is never on the critical path |

Every row answered "unsafe" or "unknown" gets one of two outcomes:
- a fix, or
- a test that pins the current behavior, plus a known-limitation entry in the phase completion report.

## Report

- **Symptom → first divergence → violated invariant.** Put the evidence (events, ids) inline.
- **Root cause and architectural cause.** Was the abstraction itself wrong?
- **The test that now reproduces it.**
- **Fix.** Explain why it addresses the cause, not the symptom.
- **Coverage gap.** Explain why existing tests missed it.
