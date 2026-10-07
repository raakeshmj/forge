# 0001 — Foundation stack and boundary enforcement

- **Status:** Proposed
- **Date:** 2026-10-07
- **Phase:** 0

## Context

The docs fix PostgreSQL as the system of record, Redis for ephemeral coordination, FastAPI for the API, OpenTelemetry, and Temporal as a future backend (ARCHITECTURE §13, §20; CLAUDE.md §5). They leave open:

- the Python toolchain
- the database driver and migration approach
- how configuration and logging work
- how the inward-dependency rule (CLAUDE.md §5) is enforced

These choices are expensive to reverse once Phase 1 adds domain tables and workers.

CLAUDE.md §3 forbids dependencies with no current use, while IMPLEMENTATION_PLAN Phase 0 lists Redis and requires "Redis works". The plan ranks higher (CLAUDE.md §2), so Redis is included, but its Phase 0 use is limited to a readiness check.

## Decision

- **Python 3.12, uv, and a committed `uv.lock`.** The build backend is `uv_build` with a `src/` layout. CI and Docker install with `--locked`.
- **HTTP:** FastAPI on uvicorn. I/O in adapters is async.
- **PostgreSQL 17 through SQLAlchemy 2.x and psycopg 3.** One driver serves both the async app engine and synchronous Alembic. The choice between SQLAlchemy Core and ORM mapping is deferred to Phase 1, when the first tables appear. Either way, domain types never subclass or import SQLAlchemy.
- **Migrations:** Alembic.
  - Migrations are hand-written; autogenerate is not configured. This follows the "explicit migrations" rule in CLAUDE.md §23.
  - They live in `forge.infrastructure.migrations` so they ship in the image.
  - Each migration runs in its own transaction.
  - Tests assert a single head and run upgrade → downgrade → upgrade on a throwaway database.
  - A one-shot compose service applies them before the API starts.
- **Redis 7.4 through redis-py (asyncio), with persistence disabled.** Redis must never hold authoritative state (ARCHITECTURE §20). Its role in task dispatch is not decided here; it belongs to the Phase 1 durable-execution ADR.
- **Configuration:** a frozen stdlib dataclass loaded from `FORGE_*` environment variables.
  - Every error is reported at once.
  - Connection URLs are excluded from `repr`, because they can carry credentials.
  - No settings library is used.
- **Logging:** stdlib `logging` with a JSON formatter, one object per line.
  - A filter stamps the correlation id from a `ContextVar` in the emitting context.
  - The HTTP middleware accepts a log-safe inbound `X-Correlation-ID` or mints one, and logs the request path without the query string.
- **Health:** `/health/live` checks nothing. `/health/ready` runs dependency checks concurrently under a timeout.
  - Expected failures are typed (`DependencyUnavailableError`) and return 503 with the driver's error type.
  - Any other exception propagates (CLAUDE.md §24).
- **Quality gates:**
  - ruff, including `BLE` (no blind except), `S` (security), `ASYNC` and `T20`
  - `mypy --strict`
  - pytest with warnings as errors, and the anyio plugin for async tests
  - Integration tests are marked and **fail rather than skip** when services are missing.
- **Boundary enforcement:** import-linter contracts in `pyproject.toml`, run in CI.
  - `forge.domain` may not import adapters, frameworks or drivers.
  - `forge.infrastructure` may not import the HTTP API.
  - Each phase that adds a provider SDK, `mcp`, `temporalio` or `opentelemetry` adds it to the domain's forbidden list in the same change.
- **Containers:** a multi-stage image running as non-root uid 10001. Compose host ports bind to loopback and can be overridden. Defaults are 15432, 16379 and 18000, which avoid clashes with common local stacks.
- **CI:** GitHub Actions runs the same Makefile targets used locally, in three jobs: checks, integration plus migrations against service containers, and a compose smoke test.

## Alternatives considered

- **Poetry or pip-tools:** slower and less standard for lockfiles than uv in 2026. uv also provides the interpreter.
- **asyncpg:** async only. Alembic would need a second driver or async shims.
- **pyright:** comparable. mypy strict was chosen for its plugin ecosystem and stable CI behavior.
- **pydantic-settings, structlog:** the stdlib covers the need (CLAUDE.md §25).
- **Deferring Redis to Phase 1:** better aligned with CLAUDE.md §3, but contradicts the higher-ranked IMPLEMENTATION_PLAN.
- **ORM autogenerate:** convenient, but it produces implicit migrations and needs ORM metadata before any tables exist.
- **Not enforcing boundaries in tooling:** relies on review alone, and boundary drift is the most common failure mode for this architecture.

## Consequences

- INV-2 (inward dependencies) and INV-19 (migration-only schema changes) are now checked mechanically in CI.
- INV-3 is reinforced, because Redis loses all data on restart by design.
- Phase 1 must choose a mapping style and add the first real migration. The round-trip test then starts exercising real DDL.
- Adding a heavy dependency now requires a recorded justification and an import-linter update when it is an adapter concern.
- Required tests: single-head check, migration round trip, readiness failure and timeout paths, and the config `repr` secrecy check (all present).

## Docs updated

- `docs/ARCHITECTURE.md` §20: migration approach, and Redis without persistence.
- `docs/IMPLEMENTATION_PLAN.md` Phase 0: status, and where the decisions are recorded.
- `README.md`: development quickstart and canonical commands.
