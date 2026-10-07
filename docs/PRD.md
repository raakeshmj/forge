# FORGE — Product Requirements Document

## 1. PRODUCT

### Name

FORGE

### Working expansion

FORGE — Autonomous Agent Operations & Learning Platform

### One-line description

FORGE is a platform for deploying, orchestrating, evaluating, observing, governing, and continuously improving autonomous AI agents.

---

# 2. VISION

Current AI agent systems are good at executing individual tasks but weak at operating reliably over long periods.

They commonly lack:

- durable execution
- strong recovery
- transparent reasoning trajectories
- reliable agent selection
- cost-aware routing
- structured evaluation
- competency measurement
- controlled learning
- governance
- operational observability

FORGE aims to provide the infrastructure layer required to operate agents as production software.

---

# 3. CORE THESIS

Agents are software components.

FORGE is the operating infrastructure around those components.

FORGE should make it possible to answer:

- What is this agent capable of?
- How reliable is it?
- Why was this agent selected?
- What tools did it use?
- What evidence did it use?
- How much did the mission cost?
- How much context was cached?
- Why did it fail?
- Can it recover?
- What capability is missing?
- Did it improve after learning?
- Is it safe to allow it to perform this action?

---

# 4. TARGET USERS

Primary:

- AI engineers
- platform engineers
- SRE teams
- software engineers
- research engineers
- teams operating autonomous agents

Secondary:

- enterprises deploying internal AI agents
- engineering organizations
- AI platform teams

---

# 5. PRIMARY USE CASE

A user submits a mission.

Example:

"Investigate why checkout latency increased after yesterday's deployment."

FORGE:

1. understands the mission
2. identifies required capabilities
3. creates a task graph
4. selects appropriate agents
5. selects appropriate models
6. constructs optimized context
7. executes tools
8. collects evidence
9. coordinates agents
10. evaluates the result
11. produces a conclusion
12. records the trajectory
13. updates competency measurements
14. detects capability gaps
15. optionally generates improvement candidates

---

# 6. CORE CONCEPTS

FORGE has the following primary concepts:

- Mission
- Task
- Agent
- Agent Version
- Capability
- Competency
- Skill
- Skill Version
- Tool
- Tool Version
- Model
- Model Profile
- Execution
- Checkpoint
- Event
- Evidence
- Evaluation
- Benchmark
- Policy
- Approval
- Memory

---

# 7. MISSION

A Mission represents a high-level objective.

A mission contains:

- objective
- constraints
- success criteria
- budget
- deadline
- required capabilities
- risk policy
- execution policy

A mission can contain many tasks.

A mission can use many agents.

A mission produces an execution trace and evaluation.

---

# 8. TASK

A Task is a unit of executable work.

A task contains:

- objective
- dependencies
- required capabilities
- allowed agents
- allowed tools
- budget
- deadline
- risk
- success criteria

Tasks can execute:

- sequentially
- concurrently
- conditionally

---

# 9. AGENT

An Agent is a managed autonomous worker.

An agent contains:

- identity
- role
- capabilities
- skills
- tools
- model profile
- memory profile
- policies
- budgets
- evaluation profile

Agents are versioned.

---

# 10. CAPABILITY

A Capability represents a type of work an agent can perform.

Examples:

- Kubernetes
- PostgreSQL
- networking
- distributed systems
- frontend development
- Python
- incident response
- security

Capabilities form a graph.

Capabilities can have:

- parent capabilities
- child capabilities
- prerequisites
- related capabilities
- required tools

---

# 11. COMPETENCY

Competency represents measured agent performance for a capability.

Competency must include:

- estimated ability
- uncertainty
- sample count
- recent trend
- evaluation history

Competency is evidence-based.

An agent does not determine its own competency.

---

# 12. SKILLS

A Skill is reusable procedural knowledge.

A skill can contain:

- instructions
- prerequisites
- tools
- examples
- constraints
- evaluation cases

Skills are versioned.

Candidate skill versions must be evaluated before promotion.

---

# 13. TOOLS

Tools allow agents to interact with external systems.

Tools can be:

- native FORGE tools
- MCP tools
- remote API tools

Every tool has:

- schema
- permissions
- risk
- timeout
- retry policy
- idempotency policy
- version

---

# 14. MODEL ROUTING

FORGE initially supports:

- Anthropic
- OpenAI

Model selection considers:

- task complexity
- required capability
- historical success
- cost
- latency
- context requirements
- model availability
- mission budget

The system must preserve provider-specific features.

---

# 15. CONTEXT ENGINEERING

FORGE constructs model context deliberately.

Context includes:

- system instructions
- agent identity
- policies
- skills
- tools
- memory
- mission state
- task state
- evidence
- tool results

Stable context should be separated from dynamic context.

---

# 16. PROMPT CACHING

FORGE uses provider-native prompt caching.

The system should optimize context placement to maximize cache reuse.

The system records:

- cache reads
- cache writes
- cached tokens
- cache creation tokens
- cache hit rate
- cache savings

Prompt caching is considered part of inference optimization.

---

# 17. MCP

MCP provides a standardized tool integration mechanism.

FORGE normalizes MCP tools into its internal tool representation.

MCP-specific implementation must remain behind the tool adapter boundary.

---

# 18. MEMORY

FORGE uses:

### Working memory

Current mission/task state.

### Episodic memory

Past missions and trajectories.

### Semantic/procedural memory

Knowledge, policies, and skills.

Memory retrieval must be explicit.

---

# 19. DURABLE EXECUTION

Mission execution must survive:

- worker crashes
- process restarts
- network failures
- tool failures
- model failures
- human approval waits

Execution supports:

- checkpointing
- retry
- pause
- resume
- cancellation
- idempotency

---

# 20. EVALUATION

Every mission can be evaluated.

Evaluation dimensions:

- correctness
- evidence quality
- trajectory quality
- tool selection
- efficiency
- calibration
- safety
- cost efficiency

Evaluation results are persisted.

---

# 21. LEARNING

FORGE can identify capability gaps.

Learning flow:

1. execution fails or underperforms
2. failure is analyzed
3. capability gap is identified
4. candidate skill is generated
5. evaluation cases are generated
6. candidate is evaluated
7. candidate is validated
8. candidate can be promoted
9. performance is monitored

Production behavior must not change directly from an unvalidated failure.

---

# 22. GOVERNANCE

Every tool has a risk level.

Actions can require:

- automatic execution
- policy evaluation
- human approval

High-risk actions require explicit approval.

---

# 23. OBSERVABILITY

FORGE provides visibility into:

- mission execution
- task execution
- agent execution
- model calls
- tool calls
- evaluations
- learning

OpenTelemetry is used for traces and metrics.

---

# 24. COST INTELLIGENCE

FORGE tracks:

- input tokens
- output tokens
- cached tokens
- model cost
- tool cost
- total mission cost

Important derived metrics include:

- cost per mission
- cost per successful mission
- quality per dollar

---

# 25. CONTROL-PLANE UI

The UI provides:

- dashboard
- missions
- mission detail
- agent registry
- agent detail
- capability graph
- skills
- evaluations
- benchmarks
- policies
- approvals
- observability
- costs

---

# 26. NON-GOALS

FORGE will NOT initially attempt to:

- support dozens of model providers
- replace Kubernetes
- replace Temporal
- build a custom vector database
- build a generic chatbot
- automatically rewrite production agents
- guarantee deterministic LLM output
- solve every possible agent use case

---

# 27. SUCCESS CRITERIA

FORGE is successful when it demonstrates:

1. reliable long-running agent execution
2. recoverability after failures
3. measurable agent performance
4. capability-aware routing
5. provider-native cache optimization
6. mission-level cost visibility
7. trajectory evaluation
8. controlled skill improvement
9. safe autonomous execution
10. measurable improvement after learning

---

# 28. DEFINING METRIC

The most important long-term metric is:

Learning Delta

= performance after validated learning
- performance before learning

FORGE should demonstrate that repeated experience can improve agent performance without sacrificing reliability or safety.