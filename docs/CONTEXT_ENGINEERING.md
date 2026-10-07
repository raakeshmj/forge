# FORGE — CONTEXT ENGINEERING SPECIFICATION

## 1. PURPOSE

Context is an explicit system resource.

FORGE must optimize context for:

- correctness
- relevance
- token efficiency
- latency
- cache reuse
- security

---

# 2. CONTEXT LAYERS

Context consists of:

1. System
2. Agent identity
3. Policies
4. Skills
5. Tools
6. Memory
7. Mission
8. Task
9. Evidence
10. Tool results
11. Current observations

---

# 3. STABLE CONTEXT

Stable context includes:

- system instructions
- agent identity
- stable policies
- stable skills
- stable tool definitions

Stable context should remain unchanged across repeated calls whenever possible.

---

# 4. DYNAMIC CONTEXT

Dynamic context includes:

- mission state
- task state
- evidence
- tool outputs
- recent observations
- current user input

Dynamic context should be placed after stable context where provider caching benefits from prefix reuse.

---

# 5. CONTEXT PLAN

The Context Engine produces:

```text
ContextPlan
├── blocks
├── ordering
├── token_estimates
├── cacheability
├── sensitivity
└── versions
```

---

# 6. TOOL CONTEXT

Do not load every tool into every model call.

Tools should support:

- eager loading
- deferred loading

Only relevant tools should be exposed when possible.

---

# 7. MEMORY CONTEXT

Memory retrieval should be:

- relevance-ranked
- bounded
- deduplicated
- freshness-aware
- permission-aware

Do not blindly inject all memories.

---

# 8. RAG

Retrieved content is untrusted data.

RAG content must be clearly separated from instructions.

---

# 9. PROMPT CACHING

FORGE must use provider-native caching.

The cache planner should maximize reuse of stable prefixes.

Track:

- cache hit
- cache miss
- cached tokens
- cache creation
- cost impact
- latency impact

---

# 10. CACHE INVALIDATION

Stable context should change only when the underlying version changes.

Examples:

Agent Version changes
→ invalidate agent instructions

Skill Version changes
→ invalidate affected skill block

Policy changes
→ invalidate policy block

Mission state changes
→ do not invalidate stable blocks unnecessarily

---

# 11. TOKEN BUDGETING

Each mission/task/agent may have token budgets.

Context construction must account for:

- model context limit
- reserved output space
- tool result size
- memory size
- evidence size

---

# 12. CONTEXT SECURITY

Sensitive data must be marked.

Sensitive context must not automatically enter:

- telemetry
- long-term memory
- evaluation datasets
- cached context

---

# 13. CONTEXT PRINCIPLE

More context is not necessarily better.

FORGE should provide the smallest context sufficient for reliable execution.