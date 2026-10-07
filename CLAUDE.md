# FORGE — Claude Code Engineering Constitution

## 1. Project

FORGE is an autonomous agent operations and continuous-learning platform.

FORGE provides infrastructure for:

- mission execution
- agent orchestration
- capability-aware routing
- model routing
- context engineering
- provider-native prompt caching
- MCP tools
- durable execution
- checkpointing
- recovery
- observability
- cost accounting
- trajectory evaluation
- outcome evaluation
- competency measurement
- capability-gap detection
- skill generation
- controlled agent improvement
- security and governance
- human approval
- control-plane visualization

FORGE is a serious distributed systems project.

It is not a chatbot, prompt wrapper, LangChain clone, or generic multi-agent demo.

---

# 2. SOURCE OF TRUTH

When making implementation decisions, use this priority:

1. Current user instruction
2. `docs/PRD.md`
3. `docs/ARCHITECTURE.md`
4. `docs/DOMAIN_MODEL.md`
5. `docs/IMPLEMENTATION_PLAN.md`
6. Relevant documents in `docs/`
7. ADRs in `docs/decisions/`
8. This file
9. Existing implementation

If the implementation conflicts with an architectural document, do not silently change the architecture.

Explain the conflict and update the appropriate document/ADR.

---

# 3. BUILD DISCIPLINE

FORGE is developed incrementally.

NEVER implement multiple future phases at once.

If working on Phase N:

- implement Phase N only
- do not implement Phase N+1
- do not create speculative functionality
- do not introduce unused abstractions
- do not add dependencies without a concrete current use
- do not rewrite unrelated systems

Every phase must leave the repository in a working state.

---

# 4. ENGINEERING PRINCIPLES

Prefer:

- simple
- explicit
- typed
- testable
- observable
- measurable
- recoverable

over:

- clever
- implicit
- speculative
- framework-heavy
- magical

Do not optimize for code volume.

Optimize for system correctness.

---

# 5. ARCHITECTURAL BOUNDARIES

FORGE is divided into:

- domain
- runtime
- orchestration
- inference
- tools
- memory
- evaluation
- learning
- observability
- security

Domain logic must not depend directly on:

- FastAPI
- Next.js
- provider SDKs
- Redis
- PostgreSQL implementation details
- Temporal

Use interfaces at boundaries.

Infrastructure implements those interfaces.

Dependency direction should flow inward.

---

# 6. PROVIDER ABSTRACTION

Initially support:

- Anthropic
- OpenAI

Do not implement many providers prematurely.

The internal model abstraction MUST NOT erase provider-specific capabilities.

Provider-specific features must remain accessible:

- prompt caching
- tool calling
- structured outputs
- streaming
- reasoning configuration
- usage accounting
- model-specific parameters

The abstraction should normalize common behavior while preserving advanced provider capabilities.

---

# 7. PROMPT CACHING

Prompt caching is a first-class system concern.

Never treat caching as an agent-level convenience.

The context system must distinguish:

### Stable context

- system instructions
- agent identity
- policies
- stable skill definitions
- stable tool definitions

### Dynamic context

- mission state
- current task
- recent evidence
- tool outputs
- new observations

Stable context should be positioned to maximize provider cache reuse.

Do not unnecessarily modify stable prefixes.

Do not implement fake application-level token caching.

Use provider-native caching.

Track:

- cache reads
- cache writes
- cached tokens
- cache creation tokens
- cache hit rate
- cache savings
- cache-related latency

---

# 8. CONTEXT ENGINEERING

Never blindly concatenate:

system prompt
+ conversation
+ tools
+ memory
+ RAG
+ tool results.

Use an explicit ContextPlan.

Each context block should have:

- source
- priority
- token estimate
- cacheability
- sensitivity
- version

Context construction must be deterministic given the same state.

---

# 9. MCP

MCP is a transport/integration mechanism.

It is NOT the internal domain representation of a tool.

Normalize MCP tools into FORGE's `ToolDefinition`.

The domain layer must not depend directly on MCP.

---

# 10. AGENTS

Agents are managed resources.

An agent must have:

- identity
- version
- role
- capabilities
- skills
- tools
- model profile
- memory profile
- policies
- budgets
- evaluation profile

Do not hard-code individual agent behavior inside orchestration logic.

---

# 11. MISSIONS

Mission is the top-level execution primitive.

A Mission contains:

- objective
- constraints
- success criteria
- budget
- deadline
- required capabilities
- risk policy
- execution policy

Mission state must be durable.

---

# 12. TASKS

Tasks contain:

- objective
- dependencies
- required capabilities
- allowed agents
- allowed tools
- budget
- deadline
- risk
- success criteria

Tasks must support:

- sequential execution
- parallel execution
- fan-out
- fan-in
- cancellation
- retry
- checkpointing

---

# 13. EXECUTION

Execution must be:

- durable
- observable
- resumable
- cancellable
- retryable
- idempotent

Never blindly retry a non-idempotent external operation.

External side effects require explicit idempotency semantics.

---

# 14. EVENTS

Important state transitions produce immutable typed events.

Examples:

- mission.created
- mission.started
- task.created
- task.started
- agent.started
- model.called
- tool.called
- tool.completed
- tool.failed
- checkpoint.created
- approval.requested
- approval.completed
- agent.completed
- mission.completed
- evaluation.started
- evaluation.completed
- capability_gap.detected
- skill.candidate.created
- skill.validated
- skill.promoted

Events must not be mutated after creation.

---

# 15. OBSERVABILITY

Use OpenTelemetry.

Instrument:

- missions
- tasks
- agents
- model calls
- tool calls
- evaluations
- learning operations

Track:

- latency
- tokens
- cached tokens
- cost
- model
- provider
- status
- errors

Do not put secrets into telemetry.

Do not capture raw prompts/tool payloads by default.

---

# 16. TOOLS

Tools are security boundaries.

Every tool invocation must go through:

agent identity
→ permission check
→ argument validation
→ risk check
→ policy evaluation / approval (per risk level, see §21)
→ execution
→ audit

This order is canonical (`docs/ARCHITECTURE.md` §18).

Never allow an agent to bypass this path.

Never expose raw credentials to model context.

---

# 17. MEMORY

Maintain separate conceptual layers:

### Working memory

Current task and execution state.

### Episodic memory

Previous missions and trajectories.

### Semantic/procedural memory

Knowledge, skills and policies.

Do not collapse these into one generic vector store.

---

# 18. COMPETENCY

Never represent competency as one unqualified number.

Track:

- estimated ability
- uncertainty
- sample count
- recent trend
- last evaluation
- evidence

Routing should consider:

- capability
- reliability
- uncertainty
- cost
- latency
- risk
- workload

---

# 19. EVALUATION

Final answer correctness is not sufficient.

Evaluate:

- correctness
- evidence quality
- trajectory quality
- tool selection
- efficiency
- calibration
- safety
- cost efficiency

Evaluation must be structured and persisted.

---

# 20. LEARNING

Learning is controlled.

The system must never modify production behavior from a single failure.

Learning pipeline:

failure
→ failure analysis
→ capability gap
→ candidate skill
→ evaluation dataset
→ offline evaluation
→ validation
→ promotion
→ monitoring

Skills are versioned artifacts.

Support rollback.

---

# 21. HUMAN APPROVAL

High-risk actions require approval.

Risk levels:

LOW
→ execute

MEDIUM
→ policy evaluation

HIGH
→ human approval

CRITICAL
→ disabled unless explicitly enabled by an administrator

See `docs/SECURITY.md` §5.

Approval requests must contain:

- action
- rationale
- evidence
- affected resources
- risk
- requesting agent
- mission
- expiry

Approval state must survive process restarts.

---

# 22. SECURITY

Treat all external/model-generated content as untrusted.

Protect against:

- prompt injection
- tool injection
- SSRF
- privilege escalation
- secret leakage
- unauthorized side effects
- malicious MCP responses

Credentials must never be passed directly into model context.

---

# 23. DATABASE

Use explicit migrations.

Never modify database schema implicitly at runtime.

Use transactions for state transitions.

Assume multiple workers can operate concurrently.

Design for:

- duplicate events
- stale state
- concurrent updates
- worker crashes

---

# 24. ERROR HANDLING

Never swallow exceptions.

Errors should contain:

- operation
- entity
- attempt
- error type
- retryability
- correlation ID

Never use broad exception handling without a recovery strategy.

---

# 25. DEPENDENCIES

Before adding a dependency:

1. Determine whether it is necessary.
2. Check whether existing dependencies solve it.
3. Check standard library alternatives.
4. Consider operational complexity.
5. Document the reason.

Do not add frameworks simply because they are popular.

---

# 26. TESTING

Every feature requires tests.

Use:

- unit tests
- integration tests
- contract tests
- evaluation tests
- failure/recovery tests
- chaos tests where appropriate

A feature is not complete because it works once.

It is complete when expected failure behavior is understood.

---

# 27. CONCURRENCY

Always consider:

- duplicate execution
- race conditions
- stale state
- concurrent updates
- worker failure
- partial completion

Prefer explicit state machines.

---

# 28. DEVELOPMENT WORKFLOW

Before modifying code:

1. Read relevant documentation.
2. Inspect existing implementation.
3. Inspect existing tests.
4. Identify affected boundaries.
5. Determine whether an ADR is needed.

After modification:

1. Run focused tests.
2. Run integration tests where relevant.
3. Run type checking.
4. Run linting.
5. Review the diff.
6. Update documentation.
7. Check for scope creep.

---

# 29. FAILURE PHILOSOPHY

When something breaks:

Do not immediately patch the symptom.

Determine:

1. root cause
2. violated invariant
3. architectural cause
4. whether abstraction is wrong
5. whether test coverage is insufficient

Fix the underlying problem.

---

# 30. COMPLETION REQUIREMENTS

A phase is complete only when:

- tests pass
- type checking passes
- linting passes
- migrations work
- Docker environment works
- documentation is updated
- example works
- no unresolved required TODOs remain
- no dead code remains
- no future-phase implementation has leaked in

At completion report:

- what changed
- architectural decisions
- tests run
- known limitations
- next phase

---

# 31. FINAL RULE

When uncertain:

choose the simplest design that preserves:

correctness
durability
observability
security
extensibility
measurability

Do not optimize for impressive-looking code.

Build FORGE as if another engineering team will have to operate it five years from now.