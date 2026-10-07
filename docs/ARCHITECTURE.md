# FORGE — SYSTEM ARCHITECTURE

## 1. ARCHITECTURAL MODEL

FORGE is divided into three major planes:

1. Control Plane
2. Execution Plane
3. Learning Plane

Supporting all three are:

- Persistence
- Event infrastructure
- Observability
- Security

---

# 2. HIGH-LEVEL ARCHITECTURE

```text
                         FORGE UI
                            |
                            v
                       API Gateway
                            |
                            v
                    +----------------+
                    |  CONTROL PLANE |
                    +----------------+
                    | Mission Manager |
                    | Agent Registry   |
                    | Capability Graph |
                    | Model Router     |
                    | Policy Engine    |
                    | Budget Manager   |
                    | Skill Registry   |
                    +-------+---------+
                            |
                            v
                    +----------------+
                    | EXECUTION PLANE|
                    +----------------+
                    | Scheduler       |
                    | Agent Runtime   |
                    | Context Engine  |
                    | Tool Runtime    |
                    | Checkpoints     |
                    | Sandbox         |
                    +-------+---------+
                            |
                            v
                    +----------------+
                    |     AGENTS      |
                    +----------------+
                       /    |     \
                      /     |      \
                     v      v       v
                  Models  Tools   Memory
                     \      |      /
                      \     |     /
                       v    v    v
                    Event / Trace
                         Store
                            |
                            v
                    +----------------+
                    | LEARNING PLANE |
                    +----------------+
                    | Evaluators      |
                    | Failure Analyzer|
                    | Gap Detector    |
                    | Skill Generator |
                    | Validator       |
                    | Promotion       |
                    +----------------+
```

---

# 3. CONTROL PLANE

The Control Plane manages desired state.

Responsibilities:

- mission creation
- agent registration
- capability registration
- skill registration
- model profiles
- routing policies
- budgets
- security policies
- approvals

The Control Plane does not execute long-running agent work directly.

---

# 4. EXECUTION PLANE

The Execution Plane performs actual work.

Responsibilities:

- task execution
- agent execution
- model calls
- tool calls
- context construction
- checkpointing
- retries
- cancellation
- recovery

The Execution Plane operates from state defined by the Control Plane.

---

# 5. LEARNING PLANE

The Learning Plane evaluates execution and proposes improvements.

Responsibilities:

- evaluation
- trajectory analysis
- failure analysis
- capability-gap detection
- skill generation
- benchmark generation
- candidate validation
- promotion

The Learning Plane must not directly mutate production behavior without validation.

---

# 6. MISSION FLOW

```text
User
 |
 v
Mission
 |
 v
Planner
 |
 v
Task Graph
 |
 +-------> Task A
 |
 +-------> Task B
 |
 +-------> Task C
 |
 v
Agent Router
 |
 v
Agent Runtime
 |
 +-------> Model
 |
 +-------> Tools
 |
 +-------> Memory
 |
 v
Evidence
 |
 v
Evaluation
 |
 v
Result
```

---

# 7. TASK GRAPH

Tasks form a directed acyclic graph where possible.

Example:

```text
              Investigation
                    |
        +-----------+-----------+
        |           |           |
        v           v           v
     Metrics      Traces       Code
        |           |           |
        +-----------+-----------+
                    |
                    v
               Correlation
                    |
                    v
                Evaluation
```

The scheduler executes independent tasks concurrently.

---

# 8. AGENT ROUTING

Routing is multi-objective.

Inputs:

- required capability
- agent competency
- competency uncertainty
- reliability
- cost
- latency
- workload
- risk
- model capability

Output:

- selected agent
- score
- explanation
- alternatives considered

Routing decisions are observable.

---

# 9. MODEL ROUTING

Model routing is separate from agent routing.

Agent routing answers:

"Which agent should perform this task?"

Model routing answers:

"Which model should this agent use?"

The model router considers:

- task complexity
- context size
- model capability
- cost
- latency
- historical performance
- mission budget
- provider availability

---

# 10. INFERENCE ARCHITECTURE

```text
Agent
 |
 v
Context Planner (estimate context requirements)
 |
 v
Model Router
 |
 v
Context Builder (within the selected model's limits)
 |
 v
Cache Planner (provider-specific)
 |
 v
Provider Adapter
 |
 +-------> Anthropic
 |
 +-------> OpenAI
 |
 v
Model Response
 |
 v
Usage Accounting
 |
 v
Telemetry
```

The model is selected before context is built: context size is a routing input (§9), while context limits, token budgets and cache placement depend on the selected model and provider.

Provider-specific capabilities must remain available.

---

# 11. CONTEXT ARCHITECTURE

Context is divided into:

```text
Stable Prefix
----------------
System instructions
Agent identity
Policies
Stable skills
Stable tool definitions

Dynamic Suffix
----------------
Mission
Task
Memory
Evidence
Tool results
Current observations
```

The Context Engine determines ordering.

---

# 12. TOOL ARCHITECTURE

```text
Agent
 |
 v
Tool Registry (resolve tool definition)
 |
 v
Identity + Permission
 |
 v
Risk Engine
 |
 v
Policy Engine (MEDIUM: policy evaluation, HIGH: approval, CRITICAL: deny unless enabled)
 |
 v
Tool Executor
 |
 +-------> Native Tool
 |
 +-------> MCP Tool
 |
 +-------> Remote API
 |
 v
Result
 |
 v
Audit + Telemetry
```

The security ordering in §18 is canonical.

---

# 13. DURABLE EXECUTION

FORGE defines a durable execution interface.

The runtime must support:

- start
- pause
- resume
- cancel
- retry
- checkpoint
- recover

The implementation must allow a future Temporal backend.

Domain logic must not depend directly on Temporal.

---

# 14. EVENT ARCHITECTURE

Events are immutable.

Examples:

```text
mission.created
mission.started
task.created
task.started
agent.started
model.called
tool.called
tool.completed
tool.failed
checkpoint.created
approval.requested
approval.completed
agent.completed
mission.completed
evaluation.started
evaluation.completed
capability_gap.detected
skill.candidate.created
skill.validated
skill.promoted
```

Events provide:

- audit
- debugging
- UI updates
- evaluation input
- replay support

---

# 15. MEMORY ARCHITECTURE

```text
Working Memory
      |
      v
Current execution state

Episodic Memory
      |
      v
Previous missions and trajectories

Semantic/Procedural Memory
      |
      v
Knowledge, skills, policies
```

Memory retrieval is explicit.

---

# 16. EVALUATION ARCHITECTURE

```text
Execution Trace
 |
 +----> Outcome Evaluator
 |
 +----> Evidence Evaluator
 |
 +----> Trajectory Evaluator
 |
 +----> Tool Evaluator
 |
 +----> Efficiency Evaluator
 |
 +----> Calibration Evaluator
 |
 +----> Safety Evaluator
 |
 v
Evaluation Result
```

---

# 17. LEARNING ARCHITECTURE

```text
Execution
 |
 v
Evaluation
 |
 v
Failure Analysis
 |
 v
Capability Gap
 |
 v
Candidate Skill
 |
 v
Evaluation Dataset
 |
 v
Offline Validation
 |
 v
Shadow Validation
 |
 v
Promotion
 |
 v
New Skill Version
```

Promotion requires explicit evaluation criteria.

---

# 18. SECURITY ARCHITECTURE

Security is enforced before tool execution.

```text
Agent
 |
 v
Identity
 |
 v
Permission
 |
 v
Risk
 |
 v
Policy
 |
 v
Execution
 |
 v
Audit
```

Secrets are resolved outside model context.

---

# 19. OBSERVABILITY ARCHITECTURE

OpenTelemetry spans exist for:

- Mission
- Task
- Agent
- Model
- Tool
- Evaluation
- Learning

All spans share correlation identifiers.

---

# 20. PERSISTENCE

PostgreSQL is the initial system of record.

Redis is used for:

- ephemeral coordination
- queues/streams
- transient state

PostgreSQL remains authoritative for durable domain state.

---

# 21. FUTURE DISTRIBUTION

The system should eventually support:

```text
API
 |
 +---- Control Plane
 |
 +---- Worker Pool
       |
       +---- Agent Worker
       +---- Tool Worker
       +---- Evaluation Worker
       +---- Learning Worker
```

Do not prematurely distribute everything.

---

# 22. FRONTEND ARCHITECTURE

Next.js frontend communicates with the API.

The UI must be read-model driven.

Mission execution events should be streamed to the UI.

React Flow should be used for:

- task DAGs
- agent graphs
- capability graphs

---

# 23. KEY OWNERSHIP RULE

Every persistent entity has one authoritative owner.

Examples:

Mission Service
→ Mission state

Agent Registry
→ Agent definitions

Capability Engine
→ Competency state

Skill Registry
→ Skill versions

Execution Runtime
→ Active execution state

Evaluation Service
→ Evaluation state

Policy Engine
→ Policy state

Do not create multiple writers for the same authoritative state.