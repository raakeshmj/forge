# 0007 — Evaluation: evaluators, ground truth, judges, persistence, and benchmarks

- **Status:** Proposed
- **Date:** 2026-10-08
- **Phase:** 5

## Context

Phase 5 must produce a structured evaluation for every mission across eight dimensions: correctness, evidence quality, trajectory quality, tool selection, efficiency, calibration, safety, and cost efficiency (EVALUATION.md). Results must be persisted and backed by evidence, not presented as a bare score. The docs leave several things open:

- where ground truth comes from, since a live incident mission has no reference answer
- whether LLM judges are used
- how evaluators are versioned
- how benchmarks are stored and run

## Decision

**An evaluation is an immutable, versioned record.**
- An `Evaluation` targets one mission. It holds one result per dimension for each WORK task and one per dimension for the mission.
- Each `DimensionResult` contains:
  - **score:** a number in 0–1, or `None` when there is not enough information to score honestly
  - **confidence:** how much the evaluator trusts its own score
  - **evaluator:** `name@version`
  - **findings:** structured evidence, each with a code, a message, and references to execution, turn, or tool call
  - **metrics:** the raw numbers behind the score
- Re-evaluating creates a new evaluation. Rows are never updated (triggers enforce this).
- `evaluation.started` and `evaluation.completed` events and a `forge.evaluation` span bracket each run.

**The default evaluators are deterministic.** They read the trajectory from checkpoints and events, so every score can be reproduced:

| Dimension | Score basis |
|---|---|
| Correctness | Ground-truth checks: phrases the answer must and must not contain |
| Evidence quality | Is the answer grounded in values that tools actually returned? Plus coverage of reference evidence |
| Trajectory quality | Penalties for duplicate calls, denied or invalid calls, failures, retries, and step-limit pressure |
| Tool selection | Precision and recall against expected tools, or denial and failure rates when none are expected |
| Efficiency | Model turns and tokens relative to the case's ideal, with credit for cached tokens |
| Calibration | 1 − \|stated confidence − correctness\|. Confidence is parsed from the answer |
| Safety | Denied attempts, by stage, plus a scan of answers and tool arguments for secret-like strings |
| Cost efficiency | Mean quality scaled by reference cost ÷ actual cost |

**Ground truth comes from benchmark cases.**
- A mission launched by a benchmark run is linked to its case, and the evaluators use the case's expectations.
- For any other mission, correctness and calibration are `None`, with a "no reference answer" finding, unless an LLM judge is configured. The evaluators never invent ground truth.

**The LLM judge is optional and explicit.**
- `JudgeEvaluator` scores correctness against the objective and success criteria from a rubric prompt, and must answer in JSON (`score`, `confidence`, `reasoning`).
- Judge cost is recorded on the evaluation.
- A judge using the same model as the agent produces a "self-preference risk" finding and has its confidence halved.
- The judge is never the default, so evaluations stay reproducible.

**Benchmarks are versioned datasets.**
- A benchmark is an immutable `(name, version)` holding cases with the EVALUATION §14 fields: objective, required capabilities, expected outcomes, rubric (`must_include` / `must_not_include`), expected tools, reference evidence, difficulty, risk, ideal turns, and reference cost.
- A benchmark run submits one mission per case for a chosen agent. Normal workers execute them, and the evaluation worker evaluates each one with its case's ground truth.
- A run is summarized once every mission is evaluated: pass rate, mean score per dimension, expected calibration error across cases, total cost, and cost per success.

**When evaluation runs.**
- An evaluation worker loop, part of the worker process (ARCHITECTURE §21's Evaluation Worker), evaluates terminal missions that have no evaluation yet.
- `POST /missions/{id}/evaluations` re-evaluates on demand.
- The Evaluation Service is the single writer of evaluations. The benchmark service is the single writer of benchmarks and runs.

## Alternatives considered

- **LLM judge by default:** nondeterministic, costs money on every mission, and judges tend to favor answers from their own model family.
- **One composite score:** EVALUATION.md and CLAUDE.md §18–19 rule it out.
- **Scoring correctness with no ground truth:** would make up a number.

## Consequences

- Deterministic scores are cheap and reproducible but shallow. Phrase checks can be gamed, so their confidence is capped at 0.7. Learning (Phase 7) is validated against benchmarks with ground truth, not against judge opinions.
- Competency (Phase 6) consumes these persisted dimension results as evidence.

## Docs updated

- `docs/EVALUATION.md` §2, §14: implemented levels and benchmark fields.
- `docs/DOMAIN_MODEL.md` §17–18: evaluation and benchmark structure.
