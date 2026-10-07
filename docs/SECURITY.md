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

---

# 6. CREDENTIALS

Credentials must never be inserted into model context.

Tools resolve credentials at execution time.

Models receive only the minimum necessary information.

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

---

# 14. SECURITY PRINCIPLE

The model decides.

FORGE decides what the model is allowed to do.