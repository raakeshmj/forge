---
name: architecture-review
description: Check a FORGE design, plan, or diff against docs/ARCHITECTURE.md, DOMAIN_MODEL.md and the other specs, decide whether an ADR is needed, and write ADRs in docs/decisions/. Use before designing a new component or port, when a change crosses a module or plane boundary, adds a writer to an entity, adds infrastructure, or contradicts or extends the docs, and whenever asked "does this need an ADR?".
---

# FORGE architecture review

## 1. Find the governing text

Apply the precedence order in CLAUDE.md §2. **ADRs rank below `docs/`**, so an ADR never overrides a doc by itself. The doc must be changed in the same change as the ADR.

## 2. Checks, in order of how often FORGE's architecture erodes

Report each check as conforms, violates (with file:line), or n/a.

1. **Dependency direction.** The domain imports none of: fastapi, sqlalchemy/psycopg/asyncpg, redis, anthropic, openai, mcp, temporalio, opentelemetry SDK. Use the Phase 0 import-linter contracts if they exist. Otherwise grep the imports.
2. **Single writer** (DOMAIN_MODEL §22). Any new INSERT, UPDATE or DELETE path to an entity outside its owning module is a violation. The caller has to go through the owner's command interface. If an entity has no listed owner, that is a doc gap, so write an ADR.
3. **Plane rules** (ARCHITECTURE §3–5):
   - The Control Plane runs no long-running work.
   - The Learning Plane reaches production *only* through Skill Registry promotion.
   - The Execution Plane reads desired state. It never defines it.
4. **Versioning.** Behavior is read from immutable versions (AgentVersion, SkillVersion, tool version, Benchmark version). Executions record the exact version ids. A mutable field that changes agent behavior is a violation.
5. **Provider abstraction** (CLAUDE §6). The normalized types keep a passthrough for provider-specific features: cache usage fields, reasoning configuration, structured output, streaming and tool-calling details. Dropping one is a violation.
6. **Inference order** (ARCHITECTURE §10). The model is selected before context is built. Cache planning is provider-specific.
7. **MCP containment.** MCP types exist only in the tool adapter. Everything else sees `ToolDefinition`.
8. **Persistence roles.** Postgres is authoritative. Redis holds nothing that can't be rebuilt from Postgres.
9. **State machines.** Transitions are explicit and match the DOMAIN_MODEL states. A status column with no transition guard is a violation.

Then check the cross-cutting invariants in `invariants.md`, next to this file.

## 3. Classify each finding

- **Conforms:** nothing to do.
- **Code vs docs:** change the code, or write an ADR and update the doc in the same change.
- **Docs silent:** apply the ADR test below.
- **Docs vs docs:** fix the docs and cite both passages. Don't pick one quietly in the code.

## 4. Does this need an ADR?

**Yes** if any of these is true:
- The docs are silent and the choice is expensive to reverse.
- It adds an infrastructure dependency.
- It creates or changes a port.
- It deviates from any doc.
- It adds a new cross-plane interaction.

**No** if the choice is local to one module and cheap to reverse, or a doc already decides it. In that case cite the doc instead.

The docs leave these decisions open. Check `docs/decisions/` first. If one is unresolved and the current work depends on it, write the ADR before the code.

| Open decision | First needed |
|---|---|
| Stack, plus boundary enforcement tooling | P0 |
| Role of Redis (Postgres `SKIP LOCKED` vs Redis for dispatch) | P0 |
| Durable execution semantics (leases, step recording, checkpoint granularity, signals and timers, the contract Temporal must also satisfy) | P1 |
| State vs events: outbox or event sourcing, what "replay" means, whether audit records are events | P1 |
| Idempotency classes, idempotency-key derivation, handling unknown outcomes | P1 |
| Budget units, pricing source, reservations under concurrency | P1 |
| Trajectory content storage, and trace topology for long or resumed missions | P1 |
| Principals, API authentication, tenancy | P1 (minimal) |
| Model profile vs router, block ordering by cache scope, deferred tools vs a stable prefix | P2 |
| Memory taxonomy, ownership, storage | P2 |
| Tool pipeline details (argument validation point, auditing denials) | P3 |
| Task graph mutability, conditional tasks, delegation privileges | P4 |
| Evaluation ground truth, LLM judges, evaluator versioning | P5 |
| Competency estimator, cold start, exploration | P6 |
| Skill binding (pinned vs floating), promotion authority, shadow validation, Learning Delta method | P7 |

## 5. Writing an ADR

- File: `docs/decisions/NNNN-kebab-title.md`, using the next free number and `adr-template.md` from this directory.
- Status starts as **Proposed**. Only the user accepts an ADR, so never mark it Accepted yourself.
- Edit the governing doc(s) in the same change and list them under "Docs updated".
- Never rewrite the decision in an accepted ADR. Supersede it with a new ADR and mark the old one `Superseded by NNNN`.
- Keep it short. Alternatives and consequences matter more than background.
