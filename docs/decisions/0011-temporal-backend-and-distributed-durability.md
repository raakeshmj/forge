# 0011 — Temporal backend, distributed workers, and infrastructure-failure recovery

- **Status:** Proposed
- **Date:** 2026-10-10
- **Phase:** 9

## Context

Phase 9's definition of done: missions survive worker crashes and resume correctly without duplicate side effects, across distributed workers. The plan asks for:
- a Temporal adapter
- distributed workers
- durable timers
- external events
- long-running missions
- worker recovery
- chaos testing

ADR 0002 committed to a Temporal-shaped contract. It sketched a mapping: "the agent loop is the deterministic workflow; model and tool calls are its activities".

Three facts shape this phase:

1. **Postgres is already the record of every step.** Checkpoints, the tool intent ledger, fenced leases and idempotency keys (ADR 0002) are what make resume safe. A second record of the same results would be a dual-write.
2. **Temporal history stores activity inputs and results.** If each model and tool call were an activity, raw prompts, model outputs and tool payloads would be copied into Temporal history. CLAUDE.md §15 forbids capturing these by default, and SECURITY §6 forbids putting credentials anywhere near them.
3. **The Postgres worker does not survive infrastructure failure.** A connection error in a claim, heartbeat, evaluation or learning loop propagates out of the worker's task group and kills the process. Nothing restarts it.

## Decision

**Two interchangeable execution backends, one contract.** `FORGE_EXECUTION_BACKEND` selects `postgres` (the default) or `temporal`. Both backends drive the same `ExecutionRunner` against the same Postgres state, and both rely on the same guarantees:

| Guarantee | How |
|---|---|
| Single writer | Fenced leases |
| No duplicate side effect | Record-before-act and the intent ledger |
| Durable approval waits | `WAITING_APPROVAL` with nothing held in memory |
| Recovery limit | `attempt` and `max_attempts` |

A shared contract test suite runs every scenario against both backends.

**The Temporal mapping is revised from ADR 0002.** Temporal orchestrates claims, not individual calls.
- **Workflow:** one `forge.task` workflow per task attempt, with workflow id `forge-task-{task_id}-{attempt}`. Temporal's workflow-id uniqueness deduplicates dispatch.
- **Activities:**
  - `forge.drive` runs the task's execution until it ends or parks for approval. If the task is READY, it claims it. If the execution is RUNNING with an expired lease, it takes over. If it is WAITING_APPROVAL and resumable, it resumes it.
  - The activity heartbeats to Temporal and renews the Postgres lease.
  - When the lease is still held by a worker that may be alive, the activity raises a retryable `LeaseHeld` error, and Temporal retries it with backoff.
- **Determinism:** the workflow code is deterministic and imports nothing from FORGE's runtime. Activities are referenced by name.
- **History contents:** only ids, statuses and wait durations. Prompts, outputs, arguments and credentials never enter Temporal history.

**Durable timers.**
- *Postgres backend:* approval expiry and deadline wake-ups are evaluated on every claim against stored timestamps (ADR 0010).
- *Temporal backend:* a parked task's workflow sleeps on a durable timer until the earliest of:
  - the approval's expiry
  - the mission's deadline
  - a poll interval (60 s by default)

  It then asks Postgres whether the execution can resume. The poll bounds the damage of a lost signal, because Postgres stays authoritative.
- Long waits use `continue-as-new` to keep workflow history bounded.

**External events.**
- An approval decision or a mission cancellation is committed to Postgres first. Only then is it published through a `ExecutionWakeup` port.
- The Temporal adapter signals `wake` to the affected task workflows. The Postgres backend's implementation does nothing, because workers poll anyway.
- A failed signal is logged, not raised. A signal is a latency optimisation; it is never the source of truth.

**Distributed workers.**
- Any number of worker processes, on either backend, may run against one database.
- *Postgres backend:* claims use `SKIP LOCKED`.
- *Temporal backend:* each worker runs a dispatcher. The dispatcher starts workflows for:
  - READY tasks
  - executions whose lease expired
  - parked executions that have become resumable

  Starting an already-running workflow is a no-op. A dispatcher can therefore never double-start, and it also re-adopts work whose workflow was lost, for example after a Temporal namespace reset.
- Compose runs the worker with `restart: unless-stopped`.

**Infrastructure-failure recovery.**
- The unit of work translates connection-level database errors into the domain's `StoreUnavailableError`.
- Every long-running loop (claim, heartbeat, dispatcher, evaluation, learning) backs off on that error and keeps going. Backoff is exponential and capped at 30 s.
- A heartbeat that cannot reach the database keeps trying until the lease would have expired. It then cancels the run as lost. The fence makes a late commit impossible either way.
- A release that cannot reach the database is skipped, and the lease expires naturally.

**Chaos testing.** A seeded in-process chaos suite runs many missions across several workers. It injects:
- worker crashes at every recovery point
- database outages
- stale (paused) workers
- duplicate dispatch

It then checks these invariants:
- every mission reaches a terminal state
- a NON_IDEMPOTENT effect happens at most once per idempotency key, and exactly once for every completed call
- event sequences are gap-free
- no execution is RUNNING without a lease

An end-to-end chaos test drives the Docker stack with several workers:
- SIGKILLs workers at random
- pauses one, making it stale
- restarts PostgreSQL

## Alternatives considered

- **ADR 0002's literal mapping (model and tool calls as activities):** copies sensitive payloads into Temporal history, duplicates the checkpoint store, and turns the agent loop into a second implementation that must be kept identical.
- **Replacing the Postgres runtime with Temporal:** makes Temporal a hard dependency of every deployment and test. The PRD lists "replace Temporal" as a non-goal, not "require Temporal".
- **Signals as the source of truth for approvals:** a lost signal would strand a mission until expiry.
- **One workflow per mission:** couples the orchestration graph (ADR 0006) to Temporal. Per-task workflows keep scheduling in the control plane.

## Consequences

- The Temporal backend's advantages are server-side retry and backoff, visibility into every task's workflow, and timers that cost nothing while parked. They come at the price of running Temporal.
- Within one task, Temporal history shows claims and waits, not individual model or tool calls. That detail stays in FORGE's events and traces.
- A worker crash still costs at most one lease period plus Temporal's heartbeat timeout before takeover.
- New dependency: `temporalio` (the official SDK). It is confined by import contract to `forge.infrastructure.temporal`.

## Docs updated

- `docs/ARCHITECTURE.md` §13, §21: backends and distribution.
- `docs/decisions/0002`: Temporal mapping superseded by this ADR.
- `README.md`: running on Temporal, chaos tests.
