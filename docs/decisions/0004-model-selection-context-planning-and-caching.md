# 0004 — Model selection, context planning, and provider-native caching

- **Status:** Proposed
- **Date:** 2026-10-08
- **Phase:** 2

## Context

Phase 2 adds real providers (Anthropic, OpenAI), model profiles, a model router, an explicit ContextPlan, and provider-native prompt caching (PRD §14–16, ARCHITECTURE §9–11, CONTEXT_ENGINEERING). The docs leave several things open:

- **The model profile conflicts with the router.** A profile names one model, which leaves the router nothing to choose.
- **Stable-block order.** The docs require stable blocks to come first but don't say in what order.
- **Deferred tools vs. a cached prefix.** Deferred tool loading could change the cached prefix.
- **"Sensitive context must not enter cached context."** OpenAI caches every prompt automatically, so it is unclear how this rule applies there.

## Decision

**Agent versions declare model candidates, and profiles describe them.**
- An AgentVersion has a primary `model`, ordered `fallback_models`, and `model_options` (provider-specific parameters such as Anthropic `output_config` or OpenAI `reasoning_effort`, passed through untouched).
- A `ModelProfile` gives each model's provider, context window, maximum output, price, tool support, caching mode (`EXPLICIT` for Anthropic, `AUTOMATIC` for OpenAI, `NONE`), minimum cacheable tokens, and quality tier.
- A model with no profile cannot be selected.

**Routing is deterministic and happens once, when the execution is claimed.**
- The router walks the candidates in the operator's preference order.
- It rejects a candidate that has no profile, whose provider is not configured, that lacks tool support the agent needs, whose context window cannot hold the planned stable context plus reserved output, or whose minimum call cost exceeds the remaining budget.
- It selects the first candidate that remains. The decision, with every alternative and the reason it was rejected, is recorded as a `model.routed` event.
- The model then stays fixed for the rest of the execution, because caches are per model and switching mid-run would throw away the cached prefix.
- Historical performance as a routing input arrives with Phase 6 competency.

**Context is planned explicitly.**
- `ContextPlanner` turns the execution's state, its versions, and the chosen profile into a `ContextPlan` of typed `ContextBlock`s. Each block records its key, layer, source, version, priority, token estimate, whether it is stable or dynamic, and its sensitivity.
- Stable blocks are ordered by how widely they are shared:
  1. tools
  2. the platform preamble (global)
  3. agent identity (one agent version)
  4. policies and mission framing (one mission)
- The transcript follows. It is append-only, so earlier turns remain a cacheable prefix.
- The budget is the model's context window minus its reserved output. When the transcript exceeds it, the oldest tool results are elided first, one at a time, with a placeholder that points to the checkpoint. The latest turn is never elided. A plan that still does not fit fails the execution with `CONTEXT_EXCEEDED`.
- The plan is deterministic: the same state and versions produce byte-identical requests.
- Token counts are estimated deterministically (characters divided by 4). Providers' own token counts are reconciled from the usage each call returns.

**Cache planning follows each provider's caching mode.**
- **EXPLICIT** (Anthropic): up to four `cache_control` breakpoints. They go after tools plus the preamble, after agent identity, after mission framing, and on the last message. A breakpoint is only placed where the prefix reaches the model's minimum cacheable size, and never at or after a block marked `SENSITIVE`.
- **AUTOMATIC** (OpenAI): no markers. A `prompt_cache_key` derived from the stable blocks' versions improves cache affinity. Because OpenAI caches automatically, a plan that contains a `SENSITIVE` block omits the key and is recorded as such. Keeping sensitive data out of OpenAI's cache entirely means routing it to a provider without automatic caching.
- **Deferred tool loading** (Phase 3) must keep the tools block stable. Tools are added only by appending after the cached prefix, never by editing it.

**Usage normalization.** `Usage.input_tokens` means uncached input tokens on every provider. OpenAI's `prompt_tokens` includes cached tokens, so the adapter subtracts `cached_tokens` and reports them as `cache_read_input_tokens`. Raw provider usage, including reasoning tokens, is kept in `provider_metadata`. Every `model.called` event records latency, the context plan summary, and the cache savings computed from the profile's prices.

**Provider-specific content is preserved.** Content blocks the domain does not model (Anthropic `thinking` blocks, for example) are stored as `OpaqueBlock`s in the transcript and sent back unchanged to the same provider. Other providers skip them.

**The adapters own no retries.** SDK retries are disabled, because the runtime's retry policy (ADR 0002) is the only one. HTTP 408, 409, 429, 5xx, and connection failures are classed as retryable. A refusal is final.

**Deterministic fakes.** The `demo` provider simulates prefix caching by hashing the prefix up to each breakpoint, applying its profile's minimum cacheable size and a TTL. Cache reuse can therefore be measured without network access.

## Alternatives considered

- **Routing on every turn:** destroys cache reuse and makes trajectories harder to reproduce.
- **A cost-optimizing router that ignores operator order:** it would silently override explicit agent configuration. Competency-aware scoring is Phase 6.
- **Provider tokenizers for budgeting:** these need network calls or extra dependencies. The estimate plus reconciliation against reported usage is enough for planning.
- **Dropping thinking blocks:** violates CLAUDE.md §6, and current Anthropic models reject edited history.

## Consequences

- Cache hit rate and savings are measured for every call and per execution.
- Real-provider behavior is covered by contract tests against recorded wire shapes. Live calls need credentials, so they run only when credentials are present.
- The price table is configuration with an "as of" date. A model whose prices are stale miscounts spend until the table is updated.

## Docs updated

- `docs/ARCHITECTURE.md` §9–11: points to this ADR.
- `docs/CONTEXT_ENGINEERING.md` §5, §9, §12: plan fields, breakpoint placement, and the sensitivity rule per caching mode.
- `docs/DOMAIN_MODEL.md` §6, §12: agent version model candidates, and the model profile fields.
