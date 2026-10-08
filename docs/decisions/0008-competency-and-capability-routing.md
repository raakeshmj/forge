# 0008 — Capability graph, competency estimation, reliability, and fitness-based routing

- **Status:** Proposed
- **Date:** 2026-10-08
- **Phase:** 6

## Context

Phase 6 must route on measured capability, uncertainty, reliability, cost, latency, and risk (PRD §10–11, CLAUDE.md §18, EVALUATION §8 and §11). Competency is never a single number. It has to account for:

- sample size and uncertainty
- recency
- task difficulty
- evaluator confidence
- calibration

The docs leave open:

- the estimator
- how evidence maps to capabilities
- cold start
- whether routing explores or exploits

## Decision

**The capability graph.**
- A capability has a name, a description, an optional parent, prerequisites, related capabilities, and required tools.
- An agent can serve a required capability when it declares that capability or one of its descendants, and declares every prerequisite of what it serves.
- Unknown names are allowed, and they behave as roots.
- A small built-in incident-response graph is registered idempotently, like the built-in benchmarks.

**Competency is a Beta–Bernoulli estimate per (agent, capability), recomputed from evidence.**
- Each task-scope evaluation contributes an outcome score `s`:
  - **With ground truth:** `s` is correctness.
  - **Without it:** `s` is the mean of evidence quality, trajectory quality, and tool selection, whichever are scored.
- Each contribution has a weight: `w = evaluator confidence × recency × difficulty`, where:
  - **Recency** decays with a 14-day half-life.
  - **Difficulty** weighs harder benchmark cases more: `1 + 0.25·(difficulty − 1)`.
- Each contribution adds `w·s` to α and `w·(1 − s)` to β, starting from the uniform prior Beta(1, 1).
- Evidence is attributed to the task's required capabilities, plus the benchmark capability when there is one, or to `general` when there are none. It also counts toward each ancestor at half weight.
- A competency record holds:
  - **estimated score:** the posterior mean
  - **uncertainty:** the posterior standard deviation
  - **sample count**
  - **effective samples:** Σw
  - **recent trend:** mean of the last 5 outcomes minus the mean of the 5 before them
  - **calibration:** the mean calibration score
  - **last evaluated** time
  - **supporting evaluations:** their ids
- Competency is recomputed from scratch for the affected pairs whenever an evaluation lands, in the same transaction, by the Capability Engine (its single writer). This makes it deterministic and self-correcting. A `competency.updated` event records each change.

**Reliability is per agent, computed on demand from execution history.**
- It is a Beta estimate over executions that completed versus executions that failed for reasons attributable to the agent: model failure, step limit, unknown tool outcome, or exhausted recovery. Budget, deadline, cancellation, and routing failures are excluded.
- It also includes mean cost per completed task and mean model latency.

**The fitness score.** For each eligible agent:

```
ability     = min over required capabilities of (estimate ± k·uncertainty)
reliability = (reliability estimate ± k·uncertainty)
fitness     = 0.45·ability·calibration_factor + 0.20·reliability
            + 0.15·cost_score + 0.10·latency_score − 0.10·min(1, workload/5)
```

- **Risk sets the direction of the bound:**
  - LOW-risk missions use the upper bound, `+` with k = 1, which explores uncertain agents.
  - MEDIUM and above use the lower bound, `−` with k = 1, which prefers proven agents.
- **Cost and latency scores** compare each agent to the cheapest and fastest candidate. Agents without history score 0.5.
- **Calibration factor:** an agent whose stated confidence runs ahead of its accuracy (mean calibration below 0.8) is penalized by its calibration shortfall.
- **No randomness:** ties break by workload, then name.
- `task.routed` records every candidate's fitness breakdown.

## Alternatives considered

- **Elo or IRT:** need many head-to-head comparisons or calibrated item difficulties, which FORGE does not have yet. Beta–Bernoulli is transparent and handles sparse data honestly.
- **Thompson sampling:** good for exploration, but nondeterministic. Deterministic UCB/LCB, chosen by risk, gives the same trade-off reproducibly.
- **Incremental updates:** cheaper, but they drift if evidence changes. Recomputation is exact and bounded per pair.

## Consequences

- Cold-start agents compete on LOW-risk work and earn evidence. High-risk work goes to proven agents.
- Weights are configuration in code. Phase 11 benchmarks measure their effect.

## Docs updated

- `docs/DOMAIN_MODEL.md` §7–8: capability and competency fields as implemented.
- `docs/ARCHITECTURE.md` §8: points to this ADR.
