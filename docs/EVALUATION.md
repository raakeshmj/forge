# FORGE — EVALUATION SPECIFICATION

## 1. PURPOSE

Evaluation determines whether agents and missions actually perform well.

FORGE must not equate:

"the model produced an answer"

with:

"the system succeeded."

---

# 2. EVALUATION LEVELS

FORGE evaluates at:

1. tool level
2. task level
3. agent level
4. mission level
5. system level
6. learning level

---

# 3. TOOL EVALUATION

Measure:

- success
- latency
- error rate
- retry count
- correctness of arguments
- policy compliance

---

# 4. TASK EVALUATION

Measure:

- completion
- correctness
- evidence
- efficiency
- tool selection
- time
- cost

---

# 5. AGENT EVALUATION

Measure:

- task success
- correctness
- trajectory quality
- tool selection
- efficiency
- calibration
- reliability
- safety

---

# 6. MISSION EVALUATION

Measure:

- mission success
- final correctness
- evidence quality
- completeness
- cost
- latency
- agent coordination
- safety

---

# 7. TRAJECTORY EVALUATION

Evaluate the complete execution:

```text
Input
→ Plan
→ Actions
→ Tool calls
→ Evidence
→ Hypotheses
→ Decisions
→ Output
```

Do not evaluate only the final output.

---

# 8. CALIBRATION

Compare stated confidence with actual correctness.

Example:

Agent confidence:
0.90

Observed accuracy across comparable tasks:
0.72

The agent is overconfident.

Calibration must affect competency estimates and routing.

---

# 9. EFFICIENCY

Measure:

- model calls
- tool calls
- tokens
- cached tokens
- wall-clock time
- cost

A correct result that uses unnecessary resources should receive a lower efficiency score.

---

# 10. SAFETY

Measure:

- policy violations
- unauthorized tool attempts
- dangerous actions
- approval bypass attempts
- secret exposure

---

# 11. COMPETENCY UPDATE

After evaluation:

```text
Evaluation
→ Capability
→ Agent
→ Update competency estimate
```

Competency updates must account for:

- sample size
- uncertainty
- recency
- task difficulty
- evaluator confidence

---

# 12. LEARNING DELTA

The primary learning metric:

```text
Learning Delta
=
Performance after validated improvement
-
Performance before validated improvement
```

Track separately for:

- correctness
- efficiency
- cost
- calibration
- reliability

---

# 13. REGRESSION

Every skill promotion must check for regressions.

A skill that improves Kafka performance but damages Kubernetes performance should not be promoted without explicit policy.

---

# 14. BENCHMARKS

Benchmarks should contain:

- task
- required capabilities
- expected outcomes
- evaluation rubric
- difficulty
- risk
- reference evidence

Benchmarks must be versioned.

---

# 15. EVALUATION PRINCIPLE

FORGE should be able to prove:

"Agent X became better after Skill Y was promoted."

It should not merely claim improvement.