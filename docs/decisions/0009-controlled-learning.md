# 0009 — Controlled learning: gaps, skill candidates, validation, promotion, monitoring, rollback

- **Status:** Proposed
- **Date:** 2026-10-08
- **Phase:** 7

## Context

Phase 7 implements CLAUDE.md §20's loop: failure → failure analysis → capability gap → candidate skill → evaluation dataset → offline evaluation → validation → promotion → monitoring, with rollback. Its definition of done is measurable improvement in a controlled experiment, where the Learning Delta is performance after minus before. The constraints:

- Production behavior must never change because of a single failure (SECURITY §12).
- Skills must be versioned and reversible.
- Generated content is untrusted.

The docs leave several things open:

- whether agents pin skill versions or follow the latest promoted one
- who authorizes a promotion
- whether shadow validation is required
- where ground truth for validation comes from

## Decision

**Agents pin skills, and promotion means a new agent version.**
- `AgentVersion.skills` lists exact `(skill, version)` pairs, so an execution's behavior is fully determined by the agent version it references (ADR 0003).
- Promoting a skill creates a new immutable AgentVersion that pins it and moves the agent's `current_version` to it.
- Rollback moves `current_version` back to the previous version. Nothing is edited in place, and every past execution remains reproducible.

**The learning cycle is an explicit, durable state machine.** A `LearningCycle` records:
- the failure analysis
- the gap
- the candidate skill version
- the generated dataset
- the baseline and candidate validation runs
- the measured deltas

Its states are `ANALYZED → GAP_DETECTED → CANDIDATE_READY → VALIDATING → VALIDATED | REJECTED → PROMOTED → (MONITORING) → ROLLED_BACK`, plus `NO_GAP` and `NEEDS_DATASET`. A learning worker advances cycles whose validation runs have finished. An operator can start a cycle (`POST /learning/cycles`), or the worker can start one automatically when `FORGE_LEARNING_AUTO=true`.

**Failure analysis groups failures by capability and finding pattern.**
- A gap needs **at least two failing evaluations sharing a pattern and at least three samples**.
- Each failing evaluation must have a correctness score of at most 0.5, or some other quality score below 0.5.
- The competency's upper bound (estimate + uncertainty) must be below 0.8.
- A single failure never creates a gap.
- Failures without ground truth can still establish a gap. The cycle then waits in `NEEDS_DATASET`, because validation needs ground truth.

**Generating a candidate skill.**
- The default generator is deterministic: it maps the dominant finding patterns to procedural guidance.
- An optional LLM generator writes the guidance from the failure examples instead.
- Either way the text is treated as untrusted. The Skill Validator rejects candidates that:
  - exceed 4,000 characters
  - tell the agent to disregard instructions, policies, or approvals
  - claim permissions or reference tools the agent is not bound to
  - contain secret-like strings
- Skills enter context as a stable, versioned block after agent identity, so changing a skill invalidates only that block's cache (CONTEXT_ENGINEERING §10).

**The evaluation dataset.**
- The Evaluation Generator builds a new benchmark version from three parts:
  - the failing cases (`gap:` keys)
  - deterministic variants of them that were never used to write the skill (`heldout:` keys)
  - cases the agent already passes (`regression:` keys)
- Validation therefore measures generalization and checks for regressions, not just a fit to the cases the skill was written from.

**Offline validation is a controlled A/B comparison.**
- The baseline agent version and a draft candidate version are each run on the dataset. The draft is not current and receives no other traffic, because tasks can be pinned to an agent version.
- Promotion criteria:
  - correctness on gap and held-out cases improves by at least 0.2 (absolute)
  - no dimension on regression cases drops by more than 0.05
  - safety does not decrease
  - at least 3 cases
- Shadow validation (ARCHITECTURE §17) is satisfied by this offline A/B. Shadowing live traffic would duplicate tool side effects, so it is not done.

**Promotion.**
- Promotion is an explicit operator action (`POST /learning/cycles/{id}/promote`). It is refused unless the cycle is VALIDATED.
- Approval workflows arrive in Phase 8, and the promotion endpoint becomes an approval-gated HIGH-risk action then.
- The previously promoted version of the same skill becomes DEPRECATED.

**Monitoring and rollback.**
- After promotion, the learning worker compares the agent's post-promotion correctness on the capability (at least 3 evaluations) with the candidate's validated correctness.
- A drop of more than 0.2 triggers an automatic rollback: reverting to a known-good version is always safe.
- Operators can also roll back at any time.
- Rollback restores the previous agent version and marks the skill version DEPRECATED.

**The Learning Delta** is recorded for each cycle, per dimension and for pass rate, separately for gap, held-out, and regression cases: candidate minus baseline. After promotion, a new benchmark run measures the delta again online.

## Alternatives considered

- **Floating skill bindings:** promotion would change existing agent versions' behavior and make past executions irreproducible.
- **Automatic promotion:** violates PRD §26 ("automatically rewrite production agents"). It can become a policy option once Phase 8 approvals exist.
- **Validating on the failing cases only:** overfits and cannot detect regressions.

## Consequences

- Learning is slow and deliberate. Each cycle runs two benchmark passes.
- The deterministic demo provider follows skill text by construction, which demonstrates the pipeline end to end. With real models, the size of the improvement is an empirical question. The same validation gate measures it, and the gate stops promotion if there is no improvement.

## Docs updated

- `docs/DOMAIN_MODEL.md` §9–10: skills, versions, states, and pinning.
- `docs/ARCHITECTURE.md` §17: points to this ADR.
