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

The complete transition table is defined in Phase 1.

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

The complete transition table is defined in Phase 1.

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

Capabilities form a directed graph.

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

---

# 13. EXECUTION

Represents an actual run of a mission/task/agent.

Fields:

- id
- mission_id
- task_id
- agent_id
- agent_version
- model
- status
- started_at
- completed_at
- cost
- token_usage

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