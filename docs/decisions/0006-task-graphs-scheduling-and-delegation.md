# 0006 — Task graphs, scheduling, agent routing, delegation, and shared budgets

- **Status:** Proposed
- **Date:** 2026-10-08
- **Phase:** 4

## Context

Phase 4 coordinates several agents on one mission (PRD §5, ARCHITECTURE §6–8). It needs:

- a planner
- a task graph with dependencies
- a scheduler that runs independent tasks concurrently
- an agent router
- delegation of work and context between agents
- fan-out and fan-in, task retry, task cancellation, and recovery

The plan says the orchestration layer must compose the Phase 1 runtime rather than reimplement it. ADR 0002 deferred budget reservations for concurrent tasks to this phase. Several things are left open:

- how plans are produced and trusted
- when agents are chosen
- how results move between tasks
- what partial failure does to a mission

## Decision

**A mission is a DAG of tasks.** It can be planned in one of three ways:
1. **Explicitly:** the request lists tasks, each with a `key`, `depends_on`, `required_capabilities`, an optional `agent_id` and `allowed_tools`, and `max_attempts`.
2. **By a planner agent:** the mission starts with one `PLANNING` task assigned to that agent. Its final answer must be a JSON plan with the same fields.
3. **As a single task:** the Phase 1 default.

The planner is an ordinary agent run through the ordinary runtime. Orchestration validates its output when the planning task completes:
- keys are unique
- every dependency refers to a key in the plan
- the graph is acyclic
- there are at most 20 tasks

A plan that fails validation fails the mission with `PLAN_INVALID`.

**Plans can narrow authority, never widen it.**
- A mission may declare a `tool_scope`. Every task's `allowed_tools` must be a subset of it, and plan-proposed tools outside it are rejected.
- A task can only use tools in its assigned agent version's bindings ∩ its `allowed_tools`, so delegating work never grants a tool (SECURITY §2, agent-to-agent escalation).

**Scheduling happens inside the transactions that change task state.** No separate scheduler process exists.
- When tasks are created, tasks with no dependencies become READY and the rest stay PENDING.
- When a task completes, dependents whose dependencies have all completed move to READY. That is fan-in.
- Several dependents becoming ready together is fan-out.
- Workers run independent READY tasks in parallel, with N claim loops per process (`FORGE_WORKER_CONCURRENCY`) across any number of processes.
- The scheduler logic is the orchestration module's lifecycle functions, which the runtime already calls atomically (ADR 0003).

**Agents are routed when a task becomes READY.**
- An explicit `agent_id` is honored.
- Otherwise the router picks among agents whose current version declares every required capability. It prefers the lowest current workload (READY plus RUNNING tasks), then name, so the choice is deterministic.
- A `task.routed` event records the choice and every alternative.
- A task with no eligible agent fails (`NO_ELIGIBLE_AGENT`).
- Measured competency replaces declared capability in Phase 6.

**Delegation passes results forward as data.**
- A task's first message is its objective followed by the outputs of the tasks it depends on. Those outputs are model-generated, so they are clearly marked as untrusted data from named upstream tasks.
- The runtime asks orchestration for this brief when it claims the task.

**Task retry.**
- An execution that fails with `MODEL_FAILED`, `MAX_STEPS_EXCEEDED`, or `RECOVERY_EXHAUSTED` sends its task back to READY (RUNNING → READY) while `attempts < max_attempts`. The retry runs as a new execution, unique per `(task_id, task_attempt)`.
- Other failures are final, because repeating them cannot help or would be unsafe: budget, deadline, unknown tool outcome, no eligible model, context exceeded.

**Partial failure and cancellation.**
- A task that fails for good fails the mission. Every non-terminal task is cancelled, and running executions stop at their next step.
- Cancelling one task (`POST /missions/{id}/tasks/{task_id}/cancel`) cancels its pending dependents. A mission completes only when every task completed. Otherwise it fails, with `TASK_CANCELLED` when the cause was a cancellation.
- The runtime's step-boundary check now covers a cancelled task or a mission that has ended, not only a cancelled mission.

**Shared budgets use reservations.**
- Before each model call, the runtime asks orchestration (the Budget Manager) to reserve the call's worst-case cost: estimated input at the input price plus the maximum output at the output price.
- The reservation runs under the mission's row lock. It is granted only if spent + other reservations + this one ≤ limit. Otherwise the execution fails with `BUDGET_EXCEEDED` before the call is made.
- There is one reservation row per execution. A step commit releases it in the same transaction that records the actual spend. A crashed execution's leftover reservation is replaced when it resumes, and terminal transitions delete it, so nothing leaks.
- Parallel tasks therefore cannot overspend a mission beyond estimation error.
- Tool costs are accounted after the fact and are not reserved.

## Alternatives considered

- **A separate scheduler process:** a second writer of task state, and a polling delay before ready tasks run.
- **Agents mutating the graph through a tool:** gives model output direct write access to control-plane state. Plans are instead validated by orchestration at one well-defined point.
- **Blocking delegation (a parent waits mid-execution for its children):** needs suspendable executions and durable signals. Phase 9 adds those. A static DAG with forwarded results covers fan-out and fan-in now.
- **Checking spend without reserving:** with N parallel tasks it allows overspend of N calls.

## Consequences

- Each task-state transition does a little more work, since scheduling happens inside it. The work is bounded by the graph size (20 tasks or fewer).
- The planner's quality limits mission quality. Its output is validated structurally but not semantically; Phase 5 evaluates that.
- `executions.task_id` is no longer unique. `(task_id, task_attempt)` is.

## Docs updated

- `docs/DOMAIN_MODEL.md` §3–4: task fields, the retry edge, PENDING → FAILED.
- `docs/ARCHITECTURE.md` §6–8: points to this ADR.
