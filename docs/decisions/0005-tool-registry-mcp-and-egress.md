# 0005 — Tool registry, MCP integration, argument validation, and egress control

- **Status:** Proposed
- **Date:** 2026-10-08
- **Phase:** 3

## Context

Phase 3 makes the tool runtime secure for native, MCP, and remote tools (PRD §13, ARCHITECTURE §12, §18, SECURITY §4–10, CONTEXT_ENGINEERING §6). The docs leave several things open:

- how a tool's identity is pinned
- what FORGE trusts from an MCP server
- where argument validation sits in the pipeline
- how deferred loading interacts with prompt caching
- how remote tools avoid SSRF and credential leakage

The plan puts credential isolation in Phase 8, but executing remote tools securely needs it now. Phase 3 builds the minimal form, and Phase 8 extends it.

## Decision

**The registry is the single writer of tool definitions.**
- `tool_versions` is append-only and holds each `(name, version)` with its source (`native`, `mcp`, `remote`), source reference, schema, and the SHA-256 of the canonical definition.
- `tools` holds the pinned current version and a status (`ACTIVE` or `QUARANTINED`).
- Workers sync the registry at startup:
  - **A tool seen for the first time** is pinned.
  - **A native tool whose version is new** is pinned. Code is deployed by operators, so it is trusted.
  - **A definition that differs from the pinned one under the same version, or any MCP or remote tool whose definition changed,** is **quarantined**. Its version is recorded, and an operator must pin it explicitly (`POST /tools/{name}/pin`) before agents can use it again.
- Only `ACTIVE` tools are executable.
- `tool.registered`, `tool.quarantined`, and `tool.pinned` events audit the registry.

**What FORGE trusts from MCP.** MCP is a transport only. The domain sees `ToolDefinition` and never an MCP type.
- Each server is declared in configuration together with an **allowlist** of its tools. The configuration maps each tool to a FORGE name and gives its risk, idempotency, timeout, retry count, and whether it is deferred.
- From the server, FORGE takes only the input schema (validated as JSON Schema). It may take the description, truncated to 1,024 characters, unless configuration supplies one.
- Server annotations never set risk or permissions. Tools the server lists but configuration does not allow are ignored.
- Stdio servers run with only the environment variables their configuration names, so FORGE's credentials never reach them.
- HTTP servers' URLs must pass the egress policy.
- Results:
  - Only text content is passed on. Other content types are replaced with a marker.
  - Output over 32 KiB becomes an error result.
  - `is_error` becomes an error result.
  - Transport failures are retryable `ToolExecutionError`s.

**The pipeline gains a validation stage.** The order is identity → permission → **validation** → risk → policy → execution → audit.
- Arguments are validated against the tool's JSON Schema (Draft 2020-12) before risk is evaluated, because risk can depend on arguments.
- Invalid arguments are denied at `validation`. The error goes back to the model, which can correct the call.
- `tool.called` and `tool.denied` record a SHA-256 of the canonical arguments, so audits can match calls without storing their content.

**Deferred loading is provider-neutral.**
- A tool marked `deferred` is not offered to the model until a `tools.search` call made in the same execution returns it.
- The set of loaded tools is derived from the transcript, so it survives resumes, and it lists only tools the agent is authorized to use.
- Loaded tools are appended after the eager tools in load order. Providers render tools first, so each load invalidates the cached prefix once. This refines ADR 0004's rule that tools may be appended but not edited: the stable prefix is restored from the next turn on. Anthropic's native `defer_loading` is a later optimization.

**Egress policy for remote tools and HTTP MCP servers.**
- Only the `https` scheme is allowed, and `http` only to explicitly allowlisted hosts.
- The host must be on the tool's allowlist.
- Every resolved address is checked. Loopback, private, link-local, multicast, reserved, and cloud-metadata addresses are rejected unless the configuration sets `allow_private`.
- The connection goes to the IP address that was checked, with the original Host header and TLS SNI, so DNS rebinding cannot change the target.
- Redirects are never followed. Responses are capped.

**Minimal credential isolation.**
- Tools receive a `SecretResolver` port and resolve credential *references* at execution time.
- Secret values never appear in tool definitions, arguments, events, spans, or logs.
- Before a result is returned to the model, any occurrence of a resolved secret is replaced with `[REDACTED]`.

## Alternatives considered

- **Trusting MCP annotations for risk:** the server would decide its own authority, which SECURITY §7 forbids.
- **Silently following an MCP server's schema changes:** a tool that changes after review becomes a different tool without anyone approving it.
- **Validating arguments inside each tool:** inconsistent, and too late to inform the risk stage.
- **Deferring by editing the tool list:** breaks the append-only prefix and makes resumes nondeterministic.

## Consequences

- Adding or changing an MCP tool requires an operator action. That is safer and slower by design.
- `jsonschema` and `mcp` are new dependencies. `mcp` is confined to `forge.tools.mcp`, and an import contract enforces this.
- Phase 8 adds policy evaluation, approvals, and per-agent credential scoping on top of these seams.

## Docs updated

- `docs/ARCHITECTURE.md` §12, §18: the validation stage.
- `docs/SECURITY.md` §6, §7, §9: implemented controls.
- `docs/CONTEXT_ENGINEERING.md` §6: deferred loading.
- `docs/DOMAIN_MODEL.md` §11: tool sources, versions, and status.
