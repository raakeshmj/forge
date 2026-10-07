# 0003 — State, events, trajectory storage, and trace topology

- **Status:** Proposed
- **Date:** 2026-10-07
- **Phase:** 1

## Context

Phase 1 introduces the first persistent entities, immutable typed events, checkpoints, and OpenTelemetry. The docs leave several things open:

- whether FORGE is event-sourced
- how events stay atomic with state
- where prompt and response content lives, given that telemetry must not carry it but evaluation (Phase 5) will need it
- how a trace hierarchy (OBSERVABILITY §3) survives a mission that is resumed by a different process

## Decision

**State tables are authoritative. FORGE is not event-sourced.**
- Each transition updates its table and appends its typed events in the **same transaction**. The events table is the outbox; Phase 1 has no consumers.
- Each event has a per-aggregate `sequence`, and `(aggregate_id, sequence)` is unique. Writers hold the aggregate's row lock when they append.
- "Replay" means rebuilding read models and audits from events. It does not mean re-executing.

**Immutability is enforced by the database.** Triggers reject UPDATE and DELETE on `events`, `checkpoints` and `agent_versions`.

**Single writer per table.**
- `forge.orchestration` writes `agents`, `agent_versions`, `missions` and `tasks`.
- `forge.runtime` writes `executions`, `checkpoints` and `tool_invocations`.
- Both append events only through the event repository.
- The runtime changes mission and task state only by calling orchestration's lifecycle functions inside its own unit of work.

**Persistence style.**
- SQLAlchemy Core tables are mapped to and from frozen domain dataclasses. There are no ORM classes, so the domain never imports SQLAlchemy.
- Mutable rows use optimistic `version` columns. Executions use `fence` (ADR 0002).

**Trajectory storage.**
- The full model transcript lives in checkpoints in Postgres: instructions, messages, tool calls and tool results. That is the trajectory record later phases will read.
- Telemetry and logs carry identifiers, counts, costs and statuses only, never content (CLAUDE.md §15).
- Usage keeps the provider's cache read and cache creation token fields.
- Audit in Phase 1 means the `tool.*` events, which record actor, mission, tool, decision and outcome. A separate Audit Service is Phase 8.

**Trace topology.**
- A mission's trace id is derived from the mission's UUID, and a task's span id from the task's UUID. Every execution attempt, including one resumed by another process, emits an `forge.agent` span parented to the task span. Model and tool spans are its children.
- The task and mission spans are emitted once, by whichever transaction makes them terminal, with their recorded start times. The hierarchy therefore holds across crashes without any span staying open for the life of a mission.
- The deterministic ids come from a custom OpenTelemetry id generator, which is used only while starting those spans.
- Export goes over OTLP/HTTP when `FORGE_OTEL_EXPORTER=otlp`. Otherwise it is off.

## Alternatives considered

- **Event sourcing:** replay complexity and schema evolution of events, with no Phase 1 benefit.
- **Separate outbox table:** redundant while events are themselves append-only and transactional.
- **One trace per attempt, linked to each other:** loses the Mission → Task → Agent hierarchy that OBSERVABILITY §3 requires.
- **Prompt content in span events:** violates CLAUDE.md §15 and OBSERVABILITY §13.

## Consequences

- Events cannot drift from state, and history cannot be edited in place.
- Checkpoints hold sensitive content. Database access control and retention are now security-relevant (a retention policy is deferred).
- Mission and task spans appear only once the mission is terminal. In-flight progress is visible through agent, model and tool spans and through events.
- Phase 4 parallel tasks reuse the same scheme, with one task span per task.

## Docs updated

- `docs/OBSERVABILITY.md` §3: trace topology note.
- `docs/ARCHITECTURE.md` §14: events are written atomically with state.
