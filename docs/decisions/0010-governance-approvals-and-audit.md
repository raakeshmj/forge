# 0010 — Governance: policy engine, durable approvals, audit, operator identity, credential scoping

- **Status:** Proposed
- **Date:** 2026-10-10
- **Phase:** 8

## Context

Phase 8 must let FORGE run riskier actions safely. Its definition of done is that high-risk tool calls require durable human approval (CLAUDE.md §21, SECURITY.md). Until now, stages owned by this phase denied everything above LOW (ADR 0002, ADR 0005). The docs leave open:

- the policy language
- how an execution waits durably for a human
- what binds an approval to an action
- who counts as a human
- where audit records live
- how credentials are scoped to agents

## Decision

**Pipeline.** The order is identity → permission → validation → risk ceiling → governance → execution → audit. Governance by risk level:

| Risk | Governance |
|---|---|
| LOW | Executes |
| MEDIUM | Policy rules decide. The first matching rule wins, and no matching rule means **deny** |
| HIGH | Only an explicit `deny` rule denies it outright. "No matching rule" does not, and no rule auto-allows it. Otherwise the call needs a human approval |
| CRITICAL | Denied unless an operator has enabled that tool. Even then it needs approval |

**Policies.**
- A policy is a named, versioned set of rules. Each rule has an effect (`allow`, `deny`, or `require_approval`), tool patterns, optional agent names, and argument conditions (`eq`, `ne`, `in`, `not_in`, `prefix`, `max_length`).
- Each new version of a name supersedes the previous one. Versions are append-only.
- Evaluation is pure and deterministic over a snapshot loaded with the step.

**Durable approvals.**
- A call needing approval creates an `Approval` holding:
  - the action (tool and canonical arguments) and their SHA-256
  - the rationale (the model's text on that turn)
  - the evidence (ids of earlier tool results)
  - the affected resources (argument values)
  - the risk level, the requesting agent, the mission, and an expiry
- The execution moves to a new `WAITING_APPROVAL` status and releases its worker. Its task becomes BLOCKED, and its mission WAITING_APPROVAL.
- A worker reclaims the execution only once one of these has happened, and nothing in process memory is involved:
  - the approval was decided or expired
  - the mission ended, its deadline passed, or the task was cancelled

  Every claim first closes overdue approvals (`expire_due`, rows locked with `SKIP LOCKED`, so a decision in flight wins). Expiry is therefore a durable timer that survives restarts.
- Resuming after a decision does not increment `attempt`. Approvals are not crash recoveries and must not exhaust the recovery limit.
- When an execution ends, any of its approvals still pending are withdrawn as EXPIRED.
- On resume, the pipeline accepts the approval only if all of these hold:
  - it is APPROVED
  - it is for the same idempotency key and argument hash
  - it has not expired

  A denied or expired approval goes back to the model as a denial.
- The Approval Service is the only writer of approvals.

**Humans are operators with API tokens.**
- `FORGE_OPERATOR_TOKENS` maps tokens to operator names.
- These endpoints require an operator token, and the operator's name is recorded as the actor:
  - deciding approvals
  - writing policies
  - enabling CRITICAL tools
  - promoting and rolling back skills
  - pinning tools
- Agents have no tokens, so an agent cannot approve its own request. Approving through any path other than an operator call is impossible by construction.
- Read endpoints stay unauthenticated, and the API must stay bound to loopback or sit behind an authenticating proxy. Full API authentication and multi-tenancy are out of scope (known limitation).

**Audit.**
- An append-only `audit_records` table records every governance-relevant decision:
  - every tool authorization outcome
  - approval requests and decisions
  - policy changes
  - CRITICAL enablements
  - skill promotions and rollbacks
- Each record carries actor (agent, operator, or system), agent, mission, tool, action, target, decision, approval, reason, argument hash, and time (SECURITY §11).
- Records are written in the same transaction as the action they describe.
- Events stay as the system's history. The audit log is the queryable compliance view (`GET /audit`).

**Credential scoping.**
- Credential grants (`credential_grants` in the tool configuration) map a secret reference to the agent names allowed to use it. A reference with no grant resolves for no one, which fails closed.
- `SecretResolver.resolve(reference, agent=...)` refuses ungranted agents, and tools pass the calling agent from `ToolContext`.
- Secret values continue to be redacted from results (ADR 0005).

## Alternatives considered

- **Blocking the worker while waiting:** loses the wait on a crash and ties up capacity for hours.
- **An approval valid for any arguments:** an agent could get a benign call approved and then run a harmful one.
- **A policy-as-code engine (OPA, Cedar):** another runtime and dependency. A small rule language covers the documented needs.
- **Audit as events only:** events are internal history keyed by aggregate. Audit needs a stable, cross-cutting, queryable schema.

**Exposure.** The model is offered the tools that could pass: LOW tools, MEDIUM tools that some rule could allow, and HIGH or CRITICAL tools that could pass with approval. The governance snapshot is read at every step, so a policy change takes effect at the next step. It also changes the tool prefix, which costs one cache write.

## Consequences

- MEDIUM tools are unusable until an operator writes a policy allowing them. This is fail closed by design.
- Long approval waits keep missions in WAITING_APPROVAL. Budgets and deadlines still apply when the execution resumes, and a passed deadline resumes the execution so that it fails.
- Sibling tasks keep running while one task waits. The mission returns to RUNNING when no task is BLOCKED.
- Existing remote-tool configurations that use a credential must add `credential_grants`.
- The demo stack ships a simulated HIGH-risk `deployment.rollback` tool, so the approval flow can be demonstrated end to end.

## Docs updated

- `docs/SECURITY.md` §3–5, §11: implemented controls.
- `docs/DOMAIN_MODEL.md` §13, §19–20: execution and approval states, policy shape.
- `docs/ARCHITECTURE.md` §18: governance stage.
