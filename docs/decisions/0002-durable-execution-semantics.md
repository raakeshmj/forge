# 0002 — Durable execution semantics (Phase 1 runtime)

- **Status:** Proposed
- **Date:** 2026-10-07
- **Phase:** 1

## Context

Phase 1 must run one agent mission durably: it survives worker interruption, can be cancelled, retries safely, enforces budgets, and never blindly repeats a side effect (CLAUDE.md §13, PRD §19). The docs require a durable execution interface that a future Temporal backend can implement (ARCHITECTURE §13). They leave open how work is claimed, what is recorded when, how a resumed worker avoids repeating work, and how budgets and cancellation are enforced.

LLM output is nondeterministic, so re-calling a model on resume can produce a different trajectory.

## Decision

**Claiming and leases.**
- Workers poll Postgres with `FOR UPDATE SKIP LOCKED`. Redis is not used for dispatch.
- A worker claims either a READY task, which creates its Execution, or a RUNNING execution whose lease has expired, which is a recovery.
- Each claim increments a monotonically increasing `fence` and sets a lease using database time.
- Every write the worker makes is conditional on `(execution_id, fence, status = RUNNING)`. A worker that has lost its lease cannot commit anything, and it stops at its next write.
- A heartbeat renews the lease while a step is in flight.

**Record before act.** The agent loop is a sequence of steps, and each step commits one transaction. That transaction contains an immutable checkpoint (the next sequence number), its events, and the execution's usage and cost totals. The rules:
- A model response is committed before any tool it requests is executed.
- A tool result is committed before the model sees it.
- Resume loads the latest checkpoint and continues. Completed steps are never repeated.
- A model call interrupted before its commit is repeated. Model calls have no side effects. The interrupted call's cost is not recorded (known limitation).

**Tool idempotency.** Every tool declares `READ_ONLY`, `IDEMPOTENT` or `NON_IDEMPOTENT`.
- Before executing, the runtime commits an intent row in a ledger. The row is keyed by the deterministic idempotency key `execution_id:turn:tool_call_id`, which is passed to the tool.
- On resume, an intent with no recorded outcome means **outcome unknown**:
  - `READ_ONLY` and `IDEMPOTENT` tools are re-executed with the same key.
  - `NON_IDEMPOTENT` tools are never re-executed. The execution fails with `TOOL_OUTCOME_UNKNOWN`, and a human has to reconcile it.
- A timeout of a `NON_IDEMPOTENT` tool is treated the same way.

**Retry.**
- Model calls: transient errors and timeouts are retried with bounded exponential backoff (`ExecutionPolicy.model_max_attempts`). Once attempts are exhausted, the execution fails with `MODEL_FAILED`.
- Tool calls: retryable failures are retried only for `READ_ONLY` and `IDEMPOTENT` tools, up to the tool's `max_attempts`. A tool failure that is final (non-retryable or out of attempts) is returned to the model as an error result and recorded as `tool.failed`.

**Timeouts.**
- Each model call and each tool call has its own timeout.
- The mission deadline is checked at every step boundary. Passing it fails the execution with `DEADLINE_EXCEEDED`.
- `ExecutionPolicy.max_steps` bounds the number of model turns.

**Cancellation is cooperative.**
- Cancelling a mission moves the mission, and its task if not yet terminal, to CANCELLED.
- A running execution sees this at its next step boundary, or when it tries to complete, and moves to CANCELLED.
- Mission and task state machines reject CANCELLED → COMPLETED, so a cancellation that races a completion resolves under a row lock.
- An in-flight call is bounded by its timeout, not interrupted.

**Budget.**
- Costs are integer micro-USD.
- Every model and tool cost is added to the execution's totals in the same transaction as the step's checkpoint.
- At each step boundary, before any model or tool call, the mission's total spend is compared with its budget. Reaching the budget fails the execution with `BUDGET_EXCEEDED`.
- Overspend is bounded by one call. Only one execution per mission exists in Phase 1, so no reservations are needed. Concurrent tasks in Phase 4 need budget reservations; that requires a new ADR.
- A model with no entry in the price table is not called (fail closed).

**Tool authorization in Phase 1.** Every call goes through identity → permission → risk → policy → execution → audit.
- **Permission:** the tool is in the agent version's tools ∩ the task's `allowed_tools`.
- **Risk:** the tool's risk is at or below the mission's risk ceiling.
- **Policy:** the Policy Engine and Approval Service don't exist yet, so this stage **denies** anything above LOW.
- A denial is audited as `tool.denied` and returned to the model as an error result.

**Temporal mapping (Phase 9).** The agent loop is the deterministic workflow, and model and tool calls are its activities. Checkpoints correspond to recorded activity results. The ledger and idempotency keys carry over unchanged.

## Alternatives considered

- **Redis streams for dispatch:** this creates a second source of truth and a dual-write between Postgres and Redis. `SKIP LOCKED` is enough at Phase 1 scale.
- **Re-running the whole execution on resume:** this repeats model calls with different outputs and repeats side effects.
- **Event-sourced replay instead of checkpoints:** more machinery than one agent loop needs. See ADR 0003.
- **Retrying non-idempotent tools on timeout:** forbidden by CLAUDE.md §13.
- **Pre-call budget reservation:** needs a token estimate, which Phase 2 context engineering provides. Overspend bounded by one call is acceptable for Phase 1.

## Consequences

- INV-9, INV-10 and INV-11 are enforced in code and tested. Every recovery point gets crash-injection tests, and fencing gets a two-worker test.
- The cost of a model call lost to a crash is unaccounted. A pre-call intent record would fix this; it is deferred.
- Cancellation latency is bounded by a step timeout.
- Pause, approvals, durable timers and parallel tasks all build on these primitives in later phases.

## Docs updated

- `docs/DOMAIN_MODEL.md` §2, §4, §13: transition tables, Execution states, and Phase 1 task fields.
- `docs/ARCHITECTURE.md` §13: points to this ADR.
