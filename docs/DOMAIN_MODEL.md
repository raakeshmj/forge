# FORGE — DOMAIN MODEL

## 1. MISSION

Represents a user's high-level objective.

Fields:

- id
- version
- objective
- constraints
- success_criteria
- budget
- deadline
- required_capabilities
- risk_policy
- execution_policy
- status
- created_at
- updated_at

Mission owns Tasks.

---

# 2. MISSION STATES

```text
CREATED
  |
  v
PLANNING
  |
  v
READY
  |
  v
RUNNING
  |
  +----> PAUSED ------------> RUNNING (resume)
  |
  +----> WAITING_APPROVAL --> RUNNING (approval decided)
  |
  +----> FAILED
  |
  v
COMPLETED

Any non-terminal state ----> CANCELLED
```

Terminal states: COMPLETED, FAILED, CANCELLED.

The complete transition table is `MISSION_TRANSITIONS` in `src/forge/domain/mission.py`; the database constrains status values.
READY -> FAILED covers a mission whose tasks were cancelled before any ran.

---

# 3. TASK

Fields:

- id
- mission_id
- objective
- dependencies
- required_capabilities
- allowed_agents
- allowed_tools
- budget
- deadline
- risk
- success_criteria
- status

---

# 4. TASK STATES

```text
PENDING
  |
  v
READY
  |
  v
RUNNING
  |
  +----> PAUSED ----> RUNNING (resume)
  |
  +----> BLOCKED ---> RUNNING (unblocked)
  |
  +----> FAILED
  |
  v
COMPLETED

Any non-terminal state ----> CANCELLED
```

Terminal states: COMPLETED, FAILED, CANCELLED.

The complete transition table is `TASK_TRANSITIONS` in `src/forge/domain/task.py`.
RUNNING -> READY is a retry (a new execution attempt); PENDING -> FAILED means no
eligible agent could be routed.

Tasks carry a `key`, `kind` (WORK or PLANNING), dependencies, required
capabilities, `max_attempts`, and `attempts`. Budget, deadline, and risk are the
mission's, shared by all tasks through budget reservations (`docs/decisions/0006`).

---

# 5. AGENT

Fields:

- id
- name
- status
- current_version

Agents are versioned.

An Agent is identity and lifecycle only. All behavioral configuration lives in Agent Version.

---

# 6. AGENT VERSION

Represents an immutable agent configuration.

Contains:

- version
- role
- instructions
- capability bindings
- skill bindings
- tool bindings
- model profile
- memory profile
- policy bindings
- budgets
- evaluation profile

A production execution references a specific Agent Version.

Versions carry role, instructions, tool bindings, a primary model with ordered
fallback models, and provider-specific model options (which may not override
FORGE-controlled request fields). Other bindings are added by the phases that
consume them.

---

# 7. CAPABILITY

Represents an area of competence.

Examples:

- Kubernetes
- Python
- PostgreSQL
- networking
- distributed systems

Fields:

- id
- name
- description
- parent_id
- prerequisites

Capabilities form a directed graph. An agent serves a capability it declares or
that one of its declared capabilities descends from, given its prerequisites
(`docs/decisions/0008`).

---

# 8. COMPETENCY

Represents measured agent ability for a capability.

Fields:

- agent_id
- capability_id
- estimated_score
- uncertainty
- sample_count
- recent_trend
- last_evaluated
- supporting_evaluations

Competency must be derived from evidence.

Implemented as a Beta-Bernoulli estimate recomputed from task evaluations,
weighted by evaluator confidence, recency (14-day half-life), and difficulty;
the record keeps estimate, uncertainty, sample and effective counts, trend,
calibration, and supporting evaluations (`docs/decisions/0008`).

---

# 9. SKILL

Represents reusable procedural knowledge.

Fields:

- id
- name
- description
- owner
- current_version
- status

---

# 10. SKILL VERSION

Immutable artifact containing:

- instructions
- prerequisites
- examples
- tool requirements
- evaluation cases
- generated_at
- source
- parent_version
- validation_results

States:

```text
CANDIDATE
  |
  v
VALIDATED
  |
  v
PROMOTED
  |
  +----> DEPRECATED
```

CANDIDATE and VALIDATED may also become REJECTED. Skill content is immutable
(database-enforced); agents pin exact skill versions, so promotion creates a new
agent version and rollback restores the previous one (`docs/decisions/0009`).

---

# 11. TOOL

Fields:

- id
- name
- description
- schema
- source
- permissions
- risk
- timeout
- retry_policy
- idempotency
- version

Sources:

- native
- MCP
- remote

Tool versions are immutable registry records fingerprinted by their definition;
a tool is ACTIVE or QUARANTINED (`docs/decisions/0005`).

---

# 12. MODEL PROFILE

Fields:

- provider
- model
- capability requirements
- context limits
- cost model
- latency characteristics
- reasoning configuration
- caching capabilities

Implemented as `ModelProfile` (`src/forge/inference/profiles.py`): context window,
maximum output, price, tool support, caching mode, minimum cacheable tokens, and
quality tier. Reasoning configuration travels as agent version model options.

---

# 13. EXECUTION

Represents an actual run of a task by a specific agent version.

Fields:

- id
- mission_id
- task_id
- agent_id
- agent_version
- model
- status
- attempt
- started_at
- completed_at
- cost
- token_usage
- output
- failure_kind
- failure_detail

States:

```text
RUNNING
  |
  +----> WAITING_APPROVAL ---> RUNNING (decided, expired, or the mission ended)
  |
  +----> COMPLETED
  |
  +----> FAILED
  |
  +----> CANCELLED
```

A waiting execution holds no lease. Resuming it after a decision is not a crash
recovery, so its `attempt` is unchanged (`docs/decisions/0010`).

An execution is created when a worker first claims its task. A crashed worker
leaves it RUNNING under an expiring lease; recovery is a further `attempt` of the
same execution, resumed from its latest checkpoint (`docs/decisions/0002`).

Failure kinds: BUDGET_EXCEEDED, DEADLINE_EXCEEDED, MAX_STEPS_EXCEEDED,
MODEL_FAILED, TOOL_OUTCOME_UNKNOWN, RECOVERY_EXHAUSTED.

Each tool call is also recorded in a tool invocation ledger keyed by a
deterministic idempotency key, so an interrupted call's outcome is never guessed.

---

# 14. CHECKPOINT

Represents recoverable execution state.

Contains:

- execution_id
- sequence
- state
- state_version
- created_at

Checkpoints must be immutable.

---

# 15. EVENT

Immutable record of a state transition or significant execution action.

Fields:

- id
- event_type
- aggregate_id
- sequence
- timestamp
- payload
- correlation_id
- causation_id

---

# 16. EVIDENCE

Represents information supporting a decision.

Contains:

- source
- content reference
- reliability
- timestamp
- relevance
- relationship to hypothesis

---

# 17. EVALUATION

Represents assessment of an execution.

Dimensions:

- correctness
- evidence_quality
- trajectory_quality
- tool_selection
- efficiency
- calibration
- safety
- cost_efficiency

Each dimension result carries a score (or none), the evaluator's confidence,
the evaluator's name and version, structured findings that reference the
trajectory, and raw metrics. Evaluations are append-only (`docs/decisions/0007`).

---

# 18. BENCHMARK

Represents a collection of evaluation cases.

Contains:

- id
- name
- capability
- cases
- scoring rubric
- version

---

# 19. POLICY

Defines allowed behavior.

Examples:

- tools permitted
- maximum cost
- maximum runtime
- write restrictions
- approval requirements

Implemented as named, versioned, append-only rule lists (`docs/decisions/0010`).
- A rule has an effect (`allow`, `deny`, `require_approval`), tool glob patterns, optional agent names, and argument conditions (`eq`, `ne`, `in`, `not_in`, `prefix`, `max_length`).
- The newest version of each name is active. Rules are evaluated in policy-name order and the first match decides. No match means deny.
- Cost, runtime and risk ceilings stay on the mission (budget, deadline, risk policy).

---

# 20. APPROVAL

Represents a human approval request.

Contains:

- action
- rationale
- evidence
- affected resources
- risk
- requester
- mission
- expiry
- decision

States: PENDING → APPROVED | DENIED | EXPIRED. Each approval binds one exact tool
call (idempotency key and argument hash) (`docs/decisions/0010`).

---

# 21. MEMORY

Memory records belong to one of:

- working
- episodic
- semantic
- procedural

Each memory must have:

- source
- timestamp
- version
- relevance
- retention policy

---

# 22. ENTITY OWNERSHIP

Mission Service
→ Mission

Task Service
→ Task

Agent Registry
→ Agent and Agent Version

Capability Engine
→ Capability and Competency

Skill Registry
→ Skill and Skill Version

Tool Registry
→ Tool and Tool Version

Execution Runtime
→ Execution and Checkpoint

Event Store
→ Event

Evaluation Service
→ Evaluation

Benchmark Registry
→ Benchmark

Policy Engine
→ Policy

Approval Service
→ Approval