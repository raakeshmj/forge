# FORGE — SECURITY SPECIFICATION

## 1. SECURITY PRINCIPLE

All model-generated content and external data must be treated as untrusted.

Agents must never have implicit authority.

---

# 2. THREAT MODEL

FORGE must consider:

- prompt injection
- tool injection
- malicious MCP servers
- SSRF
- secret leakage
- unauthorized writes
- privilege escalation
- data exfiltration
- malicious documents
- malicious API responses
- agent-to-agent privilege escalation

---

# 3. AGENT IDENTITY

Every agent execution has an identity.

Identity determines:

- allowed tools
- allowed resources
- allowed actions
- allowed credentials

Implemented: an execution acts as its agent version (`docs/decisions/0002`). Tool
bindings, the task's allowed tools, the mission's risk ceiling, policy rules
scoped by agent name, and credential grants are all checked against it. Humans
are operators who hold API tokens. Agents hold no tokens, so an agent can never
approve its own request (`docs/decisions/0010`).

---

# 4. TOOL PERMISSIONS

Tools require explicit permissions.

Example:

```text
metrics.read
logs.read
database.read
database.write
kubernetes.read
kubernetes.write
deployment.restart
```

Agents receive only required permissions.

Implemented: a tool must be in the catalog, bound to the agent version, and
allowed by the task. Tools that could never pass are not offered to the model.

---

# 5. RISK LEVELS

Tools/actions have:

```text
LOW
MEDIUM
HIGH
CRITICAL
```

LOW:

Automatic execution.

MEDIUM:

Policy evaluation.

HIGH:

Human approval.

CRITICAL:

Explicitly disabled unless enabled by administrator.

Implemented (`docs/decisions/0010`, `src/forge/tools/pipeline.py`):

| Risk | Behavior |
|---|---|
| LOW | Executes |
| MEDIUM | Runs only if a policy rule allows it. No matching rule means deny. A rule may also require approval |
| HIGH | An explicit rule may deny it outright. Nothing auto-allows it. Otherwise each call needs a human approval |
| CRITICAL | Denied unless an operator enabled that tool (`POST /tools/{name}/critical`). Even then, each call needs approval |

Approvals:
- An approval binds one exact call: its idempotency key and the SHA-256 of its canonical arguments. An approval never carries over to different arguments or a later call.
- The request records the rationale (the model's text that turn), the evidence (earlier tool results), the affected resources, the risk, the requesting agent, the mission, and an expiry (`FORGE_APPROVAL_TTL_SECONDS`, default 24h).
- While a call waits, its execution is `WAITING_APPROVAL` and holds no worker. Its task is BLOCKED and its mission WAITING_APPROVAL.
- A decision, expiry, cancellation, or passed deadline makes the execution claimable again. Pending requests of an execution that ends are withdrawn.
- Denied and expired requests reach the model as denials.

---

# 6. CREDENTIALS

Credentials must never be inserted into model context.

Tools resolve credentials at execution time.

Models receive only the minimum necessary information.

Implemented: tools hold secret references resolved at execution time
(`SecretResolver`). Any echoed secret is redacted before a result reaches the
model (`docs/decisions/0005`). A reference resolves only for the agents named in
the tool configuration's `credential_grants`. With no grant, nothing resolves, and
the refusal is raised before any request leaves the process (`docs/decisions/0010`).

---

# 7. MCP SECURITY

MCP tools are untrusted integrations.

Validate:

- tool identity
- schema
- permissions
- output size
- network destination
- execution timeout
- returned content

MCP responses must not automatically grant permissions.

Implemented: servers and their tools are allowlisted in operator configuration,
which also sets risk and idempotency; only input schemas are taken from servers;
schema changes are quarantined until pinned; stdio servers get only configured
environment variables; results are text-only and size-capped (`docs/decisions/0005`).

---

# 8. PROMPT INJECTION

Retrieved or externally sourced content must not be treated as instructions.

Separate:

- trusted instructions
- untrusted content
- tool results
- external documents

---

# 9. SSRF

Remote tools must validate:

- URL scheme
- hostname
- IP ranges
- redirects
- private network access

Implemented in `src/forge/security/egress.py`: every resolved address is checked,
the connection is pinned to the vetted address (Host header and TLS SNI preserved),
and redirects are never followed.

---

# 10. SIDE EFFECTS

Every side-effecting operation must define:

- idempotency
- risk
- authorization
- audit requirements
- rollback behavior where possible

---

# 11. AUDIT

Record:

- actor
- agent
- mission
- tool
- action
- target
- decision
- approval
- timestamp

Audit records must be immutable.

Implemented: the `audit_records` table is append-only. A trigger rejects UPDATE,
DELETE and TRUNCATE. Each record is written in the same transaction as the action
it describes, and covers:
- every tool authorization (allowed, or denied with the stage)
- approval requests and decisions
- policy writes
- CRITICAL switches
- tool pins
- skill promotions and rollbacks

Records hold an argument hash, never argument values. Query them with `GET /audit`.
Policy versions are append-only too.

---

# 12. LEARNING SAFETY

The learning system must never automatically promote behavior based on a single failure or single evaluation.

Candidate behavior must pass validation.

Promotion must be versioned.

Rollback must be possible.

---

# 13. SECURITY TESTING

Security tests must include:

- prompt injection
- unauthorized tool invocation
- privilege escalation
- malicious MCP tool
- secret leakage
- SSRF
- approval bypass
- duplicate side effect
- malicious retrieved document

Implemented:
- `tests/integration/test_security.py` runs each threat-model attack through the real runtime, with a scripted model that obeys the attack.
- `tests/integration/test_governance.py` and `tests/e2e/test_phase8_governance.py` cover approval bypass, expiry, cancellation, CRITICAL handling, and exactly-once execution across a worker kill.
- Duplicate side effects are covered in `tests/integration/test_runtime_recovery.py`.

---

# 14. SECURITY PRINCIPLE

The model decides.

FORGE decides what the model is allowed to do.