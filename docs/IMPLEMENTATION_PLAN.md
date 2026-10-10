# FORGE — IMPLEMENTATION PLAN

## RULE

Only one phase is actively implemented at a time.

Do not implement future phases early.

Each phase must leave the repository working.

---

# PHASE 0 — FOUNDATION

Status: implemented. Stack decisions: `docs/decisions/0001-foundation-stack-and-boundary-enforcement.md`.

## Objective

Create the engineering foundation.

## Build

- repository structure
- Python project
- dependency management
- configuration
- PostgreSQL
- Redis
- Docker Compose
- migrations
- logging
- testing
- type checking
- linting
- CI
- health endpoints

## Do NOT build

- agents
- LLM calls
- orchestration
- learning
- UI
- MCP

## Definition of Done

- Docker starts
- PostgreSQL works
- Redis works
- migrations work
- tests pass
- lint passes
- type checking passes
- CI passes

---

# PHASE 1 — SINGLE AGENT RUNTIME

Status: implemented. Decisions: `docs/decisions/0002` (durable execution), `docs/decisions/0003` (state, events, tracing).

## Objective

Execute one durable agent mission.

## Build

- Mission
- Task
- Agent
- Tool
- Execution
- Checkpoint
- Event
- model invocation
- tool invocation
- retry
- timeout
- cancellation
- resume
- budget
- cost accounting
- OpenTelemetry

## Flow

```text
Mission
→ Agent
→ Model
→ Tool
→ Model
→ Result
```

## Definition of Done

A mission can survive a simulated worker interruption and resume.

---

# PHASE 2 — INFERENCE AND CONTEXT

Status: implemented. Decisions: `docs/decisions/0004`.

## Objective

Create provider-aware inference infrastructure.

## Build

- Anthropic adapter
- OpenAI adapter
- model profiles
- model router
- ContextPlan
- ContextBuilder
- CachePlanner
- usage accounting
- provider-native caching

## Measure

- tokens
- cached tokens
- cache hit rate
- cost
- latency

## Definition of Done

A repeated mission demonstrates measurable context-cache reuse.

---

# PHASE 3 — TOOL RUNTIME AND MCP

Status: implemented. Decisions: `docs/decisions/0005`. Credential references, secret redaction, and egress control were pulled forward from Phase 8 because secure remote tools need them.

## Objective

Create a secure tool execution layer.

## Build

- Tool Registry
- native tools
- MCP adapter
- schema validation
- permission checks
- risk checks
- timeout
- retries
- idempotency
- audit logging
- deferred tool loading

## Definition of Done

Agents can securely execute both native and MCP tools.

---

# PHASE 4 — MULTI-AGENT ORCHESTRATION

Status: implemented. Decisions: `docs/decisions/0006`.

## Objective

Coordinate multiple agents.

## Build

- Planner
- Task Graph
- Scheduler
- Agent Router
- Delegation Engine
- parallel execution
- fan-out
- fan-in

## Definition of Done

A mission can dynamically distribute work among multiple agents.

---

# PHASE 5 — EVALUATION

Status: implemented. Decisions: `docs/decisions/0007`.

## Objective

Measure agent behavior.

## Build

- Outcome Evaluator
- Evidence Evaluator
- Trajectory Evaluator
- Tool Evaluator
- Efficiency Evaluator
- Calibration Evaluator
- Safety Evaluator
- evaluation storage
- benchmark datasets

## Definition of Done

Every mission can produce a structured evaluation.

---

# PHASE 6 — CAPABILITY INTELLIGENCE

Status: implemented. Decisions: `docs/decisions/0008`.

## Objective

Measure and route based on agent capability.

## Build

- Capability Graph
- Competency Tracker
- Reliability Tracker
- Task Fitness Scorer
- capability-aware routing
- uncertainty tracking

## Definition of Done

Agent selection uses measured capability, uncertainty, reliability, cost, latency, and risk.

---

# PHASE 7 — LEARNING

Status: implemented. Decisions: `docs/decisions/0009`.

## Objective

Enable controlled improvement.

## Build

- Failure Analyzer
- Capability Gap Detector
- Skill Generator
- Evaluation Generator
- Skill Validator
- Skill Registry
- Promotion Manager
- rollback

## Definition of Done

A controlled experiment demonstrates measurable improvement after validated skill promotion.

---

# PHASE 8 — GOVERNANCE

Status: implemented. Decisions: `docs/decisions/0010`.

## Objective

Safely execute higher-risk actions.

## Build

- Policy Engine
- Risk Engine
- Permission Engine
- Approval Service
- Audit Service
- credential isolation

## Definition of Done

High-risk tool calls require durable human approval.

---

# PHASE 9 — PRODUCTION DURABILITY

Status: implemented. Decisions: `docs/decisions/0011`.

## Objective

Support distributed durable execution.

## Build

- Temporal adapter
- distributed workers
- durable timers
- external events
- long-running missions
- worker recovery
- chaos testing

## Definition of Done

Missions survive worker crashes and resume correctly without duplicate side effects.

---

# PHASE 10 — CONTROL PLANE UI

Status: implemented. Decisions: `docs/decisions/0012`.

## Objective

Build the operational interface.

## Pages

- Dashboard
- Missions
- Mission Detail
- Agents
- Agent Detail
- Capability Graph
- Skills
- Evaluations
- Benchmarks
- Policies
- Approvals
- Observability
- Costs

## Definition of Done

A user can observe an entire mission from planning through evaluation and inspect the resulting agent performance.

---

# PHASE 11 — BENCHMARKING AND OPTIMIZATION

## Objective

Demonstrate FORGE's measurable advantages.

Benchmark:

- single agent
- naive multi-agent
- FORGE routing
- FORGE caching
- FORGE learning

Measure:

- success
- correctness
- tool calls
- latency
- cost
- recovery
- calibration
- cache efficiency
- learning delta

---

# FINAL PRODUCT MILESTONE

A complete FORGE mission should demonstrate:

```text
Mission
→ Planning
→ Capability matching
→ Agent routing
→ Model routing
→ Context optimization
→ Cached inference
→ Tool execution
→ Durable state
→ Multi-agent coordination
→ Evaluation
→ Capability measurement
→ Failure analysis
→ Skill generation
→ Validation
→ Controlled improvement
```

The system must remain observable and governed throughout the entire lifecycle.