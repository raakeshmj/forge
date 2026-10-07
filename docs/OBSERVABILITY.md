# FORGE — OBSERVABILITY SPECIFICATION

## 1. PURPOSE

Every important action in FORGE must be observable.

The system must allow an operator to answer:

- What happened?
- Why did it happen?
- Which agent did it?
- Which model was used?
- Which tools were used?
- How much did it cost?
- How long did it take?
- Did caching help?
- Where did it fail?

---

# 2. TECHNOLOGY

Use OpenTelemetry.

Do not create a separate proprietary tracing standard.

---

# 3. TRACE HIERARCHY

```text
Mission
└── Task
    └── Agent
        ├── Model
        ├── Tool
        ├── Model
        └── Decision
```

---

# 4. MISSION SPAN

Record:

- mission_id
- mission_version
- status
- duration
- task_count
- agent_count
- total_cost
- total_tokens
- cached_tokens

---

# 5. TASK SPAN

Record:

- task_id
- mission_id
- status
- duration
- selected_agent
- required_capabilities
- cost

---

# 6. AGENT SPAN

Record:

- agent_id
- agent_version
- capabilities
- model
- status
- duration
- outcome

---

# 7. MODEL SPAN

Record:

- provider
- model
- input_tokens
- output_tokens
- cached_tokens
- cache_creation_tokens
- latency
- cost
- finish_reason
- status

Do not capture sensitive prompt content by default.

---

# 8. TOOL SPAN

Record:

- tool
- tool_version
- latency
- status
- retry_count
- risk
- permission_result
- cost

Do not log secrets.

---

# 9. EVALUATION SPAN

Record:

- evaluation_id
- evaluator
- target_execution
- score
- duration
- evaluation_version

---

# 10. LEARNING SPAN

Record:

- capability_gap
- candidate_skill
- benchmark
- before_score
- after_score
- promotion_decision

---

# 11. CORRELATION

All related operations must share:

- mission_id
- execution_id
- correlation_id
- causation_id

---

# 12. DASHBOARD METRICS

System-level:

- mission throughput
- mission success rate
- p50 latency
- p95 latency
- p99 latency
- worker failures
- recovery rate

Inference:

- token usage
- cached tokens
- cache hit rate
- model cost
- model latency

Tools:

- tool success
- tool latency
- retry rate
- policy denial rate

Agents:

- success rate
- reliability
- competency
- calibration

Learning:

- capability gaps
- skill candidates
- promotion rate
- rollback rate
- learning delta

---

# 13. SECURITY

Sensitive content must not be captured by default.

Telemetry must support:

- redaction
- sampling
- configurable content capture
- tenant isolation

---

# 14. OBSERVABILITY PRINCIPLE

Observability is not a debugging feature.

It is part of the product.

A user should be able to inspect why a mission produced its result.