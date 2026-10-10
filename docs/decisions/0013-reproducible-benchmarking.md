# 0013 — Reproducible benchmarking of FORGE's mechanisms

- **Status:** Proposed
- **Date:** 2026-10-10
- **Phase:** 11

## Context

Phase 11 must demonstrate FORGE's measurable advantages. It compares:
- a single agent
- naive multi-agent
- FORGE routing
- context optimisation
- caching
- learning

on success, correctness, evidence, tool calls, latency percentiles, tokens, cached tokens, cost per success, recovery, duplicate side effects, calibration, cache efficiency and learning delta.

A comparison is only meaningful if:
- each arm toggles a real mechanism, not a staged difference
- the baselines are ones a reasonable team would build
- the results can be reproduced
- results unfavourable to FORGE are reported like the others

No API keys are available in this environment. Live-provider numbers would also be neither reproducible nor free.

## Decision

**Harness.**
- `forge.benchmarking` (`make benchmark`) runs every arm against a fresh, fully migrated database.
- It uses the real runtime, workers, evaluation service, competency engine and learning cycle, with the deterministic demo provider.
- The demo provider simulates prefix caching (ADR 0004) and follows skill text, so the same seed gives the same correctness, tokens, cache use and cost.
- Wall-clock latency is measured but machine-dependent, and is reported with the environment.
- Ground truth is the versioned `incident-investigation@1` benchmark (ADR 0007). Each arm runs every case `--repeats` times. Each repetition is a benchmark run, so the evaluation service scores it against ground truth like any other.

**Arms, in order, all on one database.** State carries forward as it would in production.

| Arm | What runs | What it isolates |
|---|---|---|
| `single_agent` | A fresh generalist runs each case as one task | The baseline |
| `after_learning` | A real learning cycle runs on that generalist: failure analysis, gap, candidate skill, held-out A/B validation, promotion. The same cases then run again | The learning delta |
| (measurement) | The incident benchmark runs for each agent in the two-agent pool (a fresh generalist and the learned agent) | Gives routing evidence. These runs are excluded from arm metrics |
| `naive_multi_agent` | Each case runs as a two-task graph (gather → conclude). Agents are assigned round-robin from the pool, ignoring capability and measured competency | The multi-agent baseline |
| `forge_routing` | The same graph, with agents chosen by FORGE's fitness routing on measured competency, reliability, cost and uncertainty (ADR 0008) | Routing's effect |
| `naive_context` | `forge_routing`, with each request rebuilt the way blind concatenation would: a per-request dynamic header first, then everything else | Context engineering's effect. Context planning is identical; only the layout differs |
| `no_caching` | `forge_routing`, with provider caching disabled in the model profile | Caching's effect |
| `chaos` | `forge_routing`, with seeded worker crashes injected before model calls and around tool effects. `metrics.read` keeps its real READ_ONLY classification, so it is safely re-executed | Recovery rate |
| `chaos_side_effects` | `chaos`, with `metrics.read` declared NON_IDEMPOTENT (and therefore not retryable) for this arm only. Each effect is counted outside the database | Duplicate side effects, and the cost of refusing to re-run an effect whose outcome is unknown |

The naive-context baseline lives in the harness, not in the runtime, so production code gains no unoptimised mode.

The two chaos arms are separate because they answer different questions. A re-executable tool shows recovery. A non-idempotent tool shows that FORGE parks an unknown outcome as `TOOL_OUTCOME_UNKNOWN` instead of duplicating the effect, which lowers completion. Each failed mission's recorded failure kind is reported, so that cost is visible rather than inferred.

**Reproducibility.**
- Arms run with concurrency 1 by default.
- Each chaos arm draws from its own `Random(seed + offset)`.
- Two runs with the same seed produce identical results except for wall-clock latency and timestamps. An integration test asserts this.
- Raising `Config.concurrency` (a harness setting, not a CLI flag) trades this guarantee for speed.

**Metrics.** These are computed from the system of record after each arm:

| Metric | Definition |
|---|---|
| Success | Pass rate: correctness ≥ 0.99 against ground truth |
| Correctness, evidence quality | Means across missions |
| Tool calls | Per mission |
| Latency | End-to-end mission latency p50/p95/p99, and model-call latency p50/p95/p99 |
| Tokens | Input, output, cached (read) and cache-write |
| Cache hit rate | Read ÷ all prompt tokens |
| Cache savings | As recorded per call |
| Cost | Total, and cost per success |
| Calibration | Expected calibration error |
| Recovery rate | Missions that completed after at least one crash, ÷ missions that crashed |
| Duplicate rate | Idempotency keys whose effect happened more than once, ÷ keys |
| Learning delta | Pass rate after promotion minus before, with the validated correctness delta |

**Outputs.** `benchmarks/results/latest.json` holds every number, the configuration, the seed, the git commit and the environment. `docs/BENCHMARKS.md` is rendered from it, with the effects stated as differences between named arms. Unit tests cover the metric functions. An integration test runs a reduced harness and asserts the effects' direction where the mechanism guarantees it: routing beats round-robin assignment of the generalist, caching lowers cost, and chaos causes no duplicate effects.

**Learning arm.** The real learning cycle runs: cycle start, drain of the dataset's missions, the learning worker's validation step, then promotion by an operator named `benchmark-harness`. Promotion is still the operator-triggered path from ADR 0009, not an automatic one.

## Alternatives considered

- **Live-provider benchmarks:** not reproducible, cost money, and there are no keys here. `make test-live` remains the live check of caching.
- **A separate synthetic simulator:** it would measure the simulator. The harness runs the production code paths.
- **Isolated databases per arm:** they would hide the feedback loops (competency, learning) that are FORGE's point.

## Consequences

- The numbers measure FORGE's mechanisms under a deterministic model, not model quality. With a real model, correctness effects depend on that model, while cache, cost, recovery and duplicate behaviour carry over.
- Results describe one dataset of three cases. They show mechanisms working, not statistical significance across domains.
