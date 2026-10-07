---
name: implement-phase
description: Scope, run, and close out one FORGE implementation phase from docs/IMPLEMENTATION_PLAN.md (Phase 0–11). Use when starting work on a phase, when unsure whether something belongs in the current phase, and before declaring a phase complete. Produces the scope statement up front and the CLAUDE.md §30 completion report at the end.
argument-hint: "<phase-number>"
---

# Implement a FORGE phase

The phase number is the argument. If none was given, ask. Never infer it.

## 1. Scope gate (before any code)

1. Read the phase section of `docs/IMPLEMENTATION_PLAN.md`, the doc sections its Build items touch, and every ADR in `docs/decisions/` that mentions the phase or those components.
2. Post a scope statement in chat and get agreement before coding:
   - **Build:** each Build item, the CLAUDE.md §5 module it lives in, and the port(s) it needs.
   - **Not building:** the later-phase components this work will be tempted to touch (see §3), each named with its phase.
   - **Proof:** the automated test(s) that demonstrate the Definition of Done.
   - **Blocking decisions:** anything the docs leave open that this phase must settle. Run `architecture-review` and get the ADR accepted before writing code that depends on it.
3. Resolve cross-phase traps. A rule in force today can require a minimal version of a later-phase concern. Build the smallest form that satisfies the rule. Don't build the later component.

| If this phase… | Rule in force | Minimal form now |
|---|---|---|
| invokes any tool | CLAUDE §16: the path can never be bypassed | Every pipeline stage exists. A stage owned by a later phase **denies** and never passes through. Tools above LOW risk are rejected until policy and approval exist |
| retries or resumes anything | CLAUDE §13 | Every side-effecting operation declares its idempotency semantics before any retry path exists |
| calls a model | CLAUDE §6/§7, PRD §24 | Usage and cost come from the provider response. Provider-specific usage fields (cache read/creation) are kept, not dropped |
| persists or transitions state | CLAUDE §23/§27 | A migration, an explicit state machine, a transaction, and a test with two concurrent workers |
| refers to a concept owned by a later phase (skills, capabilities, approvals, memory, competency) | CLAUDE §3 | A plain identifier or data shape. No service, registry or engine |
| produces data a later phase will consume (trajectory, evidence, agent confidence, cost, cache usage) | Later phases can't recreate history | Persist it now, in a form the later phase can read |

## 2. While implementing

- If something contradicts or isn't covered by `docs/`, stop and run `architecture-review`. Never change the architecture silently.
- If you change the runtime, state machines, persistence, retries or side-effecting tools, run `failure-analysis` (pre-mortem mode) before finishing.
- If you change the tool path, MCP or remote adapters, credentials, context assembly or telemetry content, run `forge-security-review` before finishing.
- Every new dependency needs a CLAUDE.md §25 justification, recorded for the completion report.

## 3. Leakage markers

When working on Phase N, a diff that introduces any component from a phase above N needs a written justification (usually one of the trap rows above):

- **P2:** model router, ModelProfile selection, ContextPlan/ContextBuilder, CachePlanner, a second provider adapter
- **P3:** Tool Registry, MCP adapter, deferred tool loading
- **P4:** Planner, TaskGraph, Scheduler, Agent Router, delegation
- **P5:** evaluators, benchmarks, evaluation storage
- **P6:** Capability Graph, competency, reliability or fitness scoring
- **P7:** failure analyzer, gap detector, skill generator or validator, promotion, rollback
- **P8:** Policy, Risk, Permission engines as real (non-denying) logic, Approval Service, Audit Service, credential isolation layer
- **P9:** Temporal, distributed worker topology, chaos tests
- **P10:** Next.js / frontend

Also flag interfaces, parameters, config keys or tables that nothing in this phase uses.

## 4. Close-out gate

Run every check; don't assume any of them pass. Use the canonical commands defined by the Phase 0 tooling (Makefile, pyproject or CI workflow). If a check has no canonical command, report that as a Phase 0 gap. Don't improvise one.

- [ ] Tests, type checking and lint pass. Record the exact commands and the pass/fail counts.
- [ ] Migrations upgrade from an empty database and downgrade one step, on the Docker Postgres.
- [ ] `docker compose up` works from a clean state, and the health endpoints respond.
- [ ] The DoD is shown by an automated test, not a manual run. For example, Phase 1 kills the worker mid-mission and asserts it resumes with no duplicate side effects.
- [ ] The leakage scan (§3) is clean or justified. No dead code, and no required TODOs.
- [ ] Every doc statement the code no longer matches is updated, or the deviation is recorded in an ADR together with the doc update.

## 5. Completion report

Post in chat with these sections:

- **Changed:** components and modules
- **Decisions:** links to ADRs, plus any decision too small for an ADR, in one line each
- **Tests run:** commands and results, verbatim
- **Dependencies added:** each with its reason
- **Known limitations:** include every stub that fails closed, and the phase that replaces it
- **Deferred:** item → target phase
- **Next phase:** the blocking decisions it will need first
