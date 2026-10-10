# 0012 — Control-plane UI: read models, live events, and operator actions

- **Status:** Proposed
- **Date:** 2026-10-10
- **Phase:** 10

## Context

Phase 10's definition of done: a user can observe an entire mission, from planning through evaluation, and inspect the resulting agent performance.

ARCHITECTURE §22 fixes these choices:
- Next.js
- a read-model-driven UI
- mission events streamed to the UI
- React Flow for task DAGs, agent graphs and capability graphs

The plan lists thirteen pages, and the API already serves most of the underlying state. The docs leave open:
- where aggregates (latency percentiles, cost breakdowns, cache efficiency) are computed
- how events reach the browser
- how a human acts from the UI without leaking credentials
- what the UI may show, given CLAUDE.md §15 (no raw prompts or tool payloads by default)

## Decision

**Layout and stack.**
- `ui/` is a standalone Next.js 16 (App Router) TypeScript application built and tested with npm.
- Dependencies:
  - runtime: `next`, `react`, `react-dom`, `@xyflow/react` (React Flow)
  - development: TypeScript, ESLint with `eslint-config-next`, Vitest
- No CSS framework and no client state library. Pages are server components that fetch from the FORGE API at request time. Only interactive views (graphs, the live event feed, forms) are client components.

**Read models live in the API, not in the browser.**
- New read endpoints are backed by a query-side `insights` repository that runs SQL aggregates over the system of record:
  - `GET /missions`: recent missions with cost, tokens, and task counts
  - `GET /evaluations`
  - `GET /benchmark-runs`
  - `GET /insights`: model and tool usage, latency p50/p95/p99, tokens and cached tokens, cache hit rate and savings, failures, cost by agent, model and day
  - `GET /agents/{id}/usage`: model usage, cost, failure clusters, and evaluation dimensions for one agent
- The UI derives only presentation views, such as the per-mission timeline and the DAG layout, in pure, unit-tested TypeScript.

**Live events.**
- `GET /missions/{id}/events/stream` is a Server-Sent Events stream. Each poll re-reads the mission's events and sends the ones not yet sent, identified by event id.
- The events table has no global commit-ordered cursor; timestamps from concurrent transactions can commit out of order. Re-reading by mission therefore never skips an event.
- The stream closes once the mission is terminal.
- The browser reaches the API only through the UI server's `/api/forge/*` proxy, so no CORS is needed and the API stays bound to loopback.

**Operator actions.**
- The UI acts only through the same operator-token endpoints as any client (ADR 0010).
- An operator enters their token in the approval form. A server action forwards it once as a bearer header.
- The token is never stored in a cookie, local storage, logs or the page.
- The UI server holds no credentials of its own.

**Content.** The UI shows what the API already exposes:
- events, whose payloads carry hashes, ids, usage and timings, not arguments or results
- a mission's final output and evaluation findings
- approval requests, which must show the exact action to the human deciding it

It shows no prompts, checkpoint contents or tool results.

**Testing.**
- Vitest unit tests cover the read-model derivations.
- An end-to-end test runs the built UI against the running stack. It drives a planned mission through planning, execution and evaluation, then asserts each page server-renders that mission's data. It also checks that the event stream delivers through the proxy.
- No browser automation is used. Every page's content is server-rendered, so HTML assertions observe what a user sees. The only client-side behaviours are layout and the live feed, and those are covered by unit tests and the stream test.

## Alternatives considered

- **Computing aggregates in the browser from raw lists:** this pulls unbounded data into every page view, and it duplicates SQL that the system of record does better.
- **WebSockets:** the feed is one-directional, and SSE is simpler and reconnects natively.
- **Browser-side API calls with CORS:** this exposes the API to the browser origin and requires CORS configuration on a loopback-only service.
- **Storing the operator token in a cookie for convenience:** it widens the window in which a stolen session could approve actions.
- **Playwright end-to-end tests:** a browser download and a second test runner for pages that are server-rendered anyway.

## Consequences

- The UI is only as available as the API it proxies. It has no state of its own.
- Read endpoints remain unauthenticated (ADR 0010). The UI inherits that limitation and must likewise stay behind loopback or an authenticating proxy.
- Compose gains a `ui` service on `127.0.0.1:13000`.

## Docs updated

- `docs/ARCHITECTURE.md` §22.
- `README.md`: the control plane.
