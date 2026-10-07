# FORGE invariant catalog

This index points to invariants that are spread across the docs. The source column is authoritative. Rows marked *derived* follow from the cited rules but aren't stated in the docs. Once an ADR confirms one, cite the ADR here.

| ID | Invariant | Source |
|---|---|---|
| INV-1 | Each persistent entity has exactly one writer | ARCHITECTURE §23, DOMAIN_MODEL §22 |
| INV-2 | Dependencies point inward. The domain is free of infrastructure | CLAUDE §5, ARCHITECTURE §13 |
| INV-3 | Postgres is authoritative. Redis state can always be rebuilt | ARCHITECTURE §20 |
| INV-4 | Events, checkpoints, audit records and versions are immutable once written | CLAUDE §14, DOMAIN_MODEL §10/§14, SECURITY §11 |
| INV-5 | Every tool call goes identity → permission → risk → policy/approval → execute → audit, with no bypass | CLAUDE §16, ARCHITECTURE §18 |
| INV-6 | A pipeline stage that isn't implemented yet denies. It never passes through | *derived* from INV-5 + CLAUDE §3 |
| INV-7 | Credentials never reach model context, telemetry, memory, eval data or cached context | CLAUDE §22, SECURITY §6, CONTEXT §12 |
| INV-8 | External and model-generated content is data, never instructions | SECURITY §1/§8, CONTEXT §8 |
| INV-9 | A non-idempotent side effect is never blindly retried, and every side-effecting operation declares its idempotency | CLAUDE §13, SECURITY §10 |
| INV-10 | A model response or tool result is persisted before it influences the next step. Resume replays recorded results instead of calling again | *derived* from PRD §19 + Phase 1/9 DoD |
| INV-11 | State changes go through explicit state machines, inside transactions, and stay correct under concurrent workers, duplicates and stale reads | CLAUDE §23/§27 |
| INV-12 | Executions reference exact immutable versions of agent, skills and tools | DOMAIN_MODEL §6, CONTEXT §10 |
| INV-13 | Context building is deterministic for a given state and set of versions. The stable prefix changes only when a version changes | CLAUDE §8, CONTEXT §10 |
| INV-14 | Caching is provider-native only. There is no application-level token cache | CLAUDE §7 |
| INV-15 | Competency is derived from evidence, has several fields, and is never self-reported | CLAUDE §18, PRD §11 |
| INV-16 | No production behavior change comes from a single failure or evaluation. Promotion is validated, regression-checked, versioned and reversible | CLAUDE §20, SECURITY §12, EVALUATION §13 |
| INV-17 | HIGH risk requires a durable human approval. CRITICAL is denied unless an administrator enables it | CLAUDE §21, SECURITY §5 |
| INV-18 | Telemetry carries no secrets, and no raw prompts or payloads by default | CLAUDE §15, OBSERVABILITY §13 |
| INV-19 | Schema changes happen only through migrations | CLAUDE §23 |
| INV-20 | Errors carry operation, entity, attempt, type, retryability and correlation id, and are never swallowed | CLAUDE §24 |
