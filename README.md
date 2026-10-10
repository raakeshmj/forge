# FORGE

FORGE is an autonomous agent operations and continuous-learning platform. Start with [`CLAUDE.md`](CLAUDE.md) and [`docs/`](docs/). Architecture decisions are recorded in [`docs/decisions/`](docs/decisions/).

**Status:** Phases 0-9 are implemented: durable multi-agent orchestration (planned task graphs, capability routing, parallel fan-out/fan-in, retries, shared budgets) over a provider-aware inference layer with prompt caching and a secure tool runtime (registry, MCP, remote API tools), with structured evaluation of every mission, versioned benchmarks, capability-aware routing on measured competency, controlled learning (validated, reversible skill promotion), and governance (policies, durable human approval of high-risk calls, append-only audit, per-agent credential grants), on either of two durable execution backends (PostgreSQL, or Temporal) that survive worker crashes, stale workers, and database outages without duplicating side effects. See [`docs/IMPLEMENTATION_PLAN.md`](docs/IMPLEMENTATION_PLAN.md).

## Requirements

- [uv](https://docs.astral.sh/uv/). It installs Python 3.12 if needed.
- Docker with Compose v2

## Quickstart

```sh
make install           # create .venv from uv.lock
make check             # lint, types, import boundaries, unit tests
make up                # build and start Postgres, Redis, migrations, API; waits until healthy
make test-integration  # integration, recovery, and migration tests against the running stack
make e2e               # demos + chaos drill: worker kills, stale worker, PostgreSQL restart
make up-temporal && make e2e-temporal  # the same, on the Temporal backend
curl localhost:18000/health/ready
make down              # stop and delete volumes
```

The Makefile is the source of canonical commands. CI runs the same targets.

## Running a mission

```sh
agent=$(curl -s localhost:18000/agents -H 'content-type: application/json' -d '{
  "name": "investigator", "role": "incident investigator",
  "instructions": "Investigate with the tools available.",
  "model": {"provider": "demo", "name": "demo-investigator-1"}, "tools": ["metrics.read"]}' | jq -r .agent_id)
mission=$(curl -s localhost:18000/missions -H 'content-type: application/json' -d "{
  \"objective\": \"Why did checkout latency increase?\", \"agent_id\": \"$agent\",
  \"budget_micro_usd\": 100000}" | jq -r .id)
curl -s localhost:18000/missions/$mission | jq          # status, result, usage, cost
curl -s localhost:18000/missions/$mission/events | jq   # the immutable event history
curl -s -X POST localhost:18000/missions/$mission/cancel
```

A mission is planned in one of three ways: `agent_id` (one task), `tasks` (an
explicit graph of tasks with `key`, `depends_on`, `required_capabilities`), or
`planner_agent_id` (a planner agent proposes the graph, which FORGE validates).
Agents declare `capabilities`; tasks are routed to capable agents with the least
work. `make e2e` includes a planned, fanned-out mission on the demo provider.

Workers evaluate every finished mission on eight dimensions
(`GET /missions/{id}/evaluations`). `POST /benchmarks/incident-investigation/runs`
with an `agent_id` measures that agent against ground truth
(`GET /benchmark-runs/{id}` for the summary).

Evaluations feed per-capability competency estimates with uncertainty
(`GET /agents/{id}`); routing prefers measured ability, reliability, cost, and
latency over declarations alone. `GET /capabilities` shows the capability graph.

`POST /learning/cycles` with an `agent_id` and `capability` analyses failures;
a real gap yields a candidate skill, validated by an A/B run on held-out cases.
`POST /learning/cycles/{id}/promote` moves the agent to a version pinning the
skill; `/rollback` restores the previous version. The demo provider follows skill
text deterministically, so the integration tests show the full loop: the demo
investigator goes from 1/3 to 3/3 on the incident benchmark after promotion.

The `demo` provider is a deterministic local model that simulates prefix caching.
Agents can name Anthropic or OpenAI models (with `fallback_models`); the worker
enables a provider only when its credentials (`ANTHROPIC_API_KEY`,
`OPENAI_API_KEY`) are present, and routes around providers that are not.
`make test-live` exercises real prompt caching when credentials are set.

## Tools

Native tools (`metrics.read`, `tools.search`) are always registered. MCP servers
and remote API tools are declared in a JSON file named by `FORGE_TOOL_CONFIG`
(format in `src/forge/tools/config.py`); credentials are referenced by name and
read from `FORGE_SECRET_<NAME>` at execution time. `GET /tools` shows each
tool's pinned version and status; `POST /tools/{name}/pin` approves a changed one.
A credential resolves only for the agents listed under `credential_grants`.

## Governance

Every tool call passes identity → permission → validation → risk → governance →
execution → audit ([`docs/decisions/0010`](docs/decisions/0010-governance-approvals-and-audit.md)).

| Risk | What happens |
|---|---|
| LOW | Runs |
| MEDIUM | Runs only if a policy allows it |
| HIGH | Waits for a human approval of that exact call |
| CRITICAL | Denied unless an operator enables the tool, and then also needs approval |

Humans are operators configured on the API with `FORGE_OPERATOR_TOKENS=name:token,...`
(tokens of 32 or more characters). Writes require `Authorization: Bearer <token>`:

| Endpoint | Purpose |
|---|---|
| `POST /approvals/{id}/decision` | Approve or deny a request |
| `POST /policies` | Write a new policy version |
| `POST /tools/{name}/critical` | Enable or disable a CRITICAL tool |
| `POST /tools/{name}/pin` | Pin a changed tool version |
| `POST /learning/cycles/{id}/promote`, `/rollback` | Promote or roll back a skill |

```sh
export FORGE_OPERATOR_TOKENS="alice:$(openssl rand -hex 24)"  # then `make up`
curl -s 'localhost:18000/approvals?status=PENDING' | jq   # action, rationale, evidence, expiry
curl -s -X POST localhost:18000/approvals/$id/decision -H "Authorization: Bearer ${FORGE_OPERATOR_TOKENS#*:}" \
  -H 'content-type: application/json' -d '{"decision": "approve", "reason": "on-call approved"}'
curl -s "localhost:18000/audit?mission_id=$mission" | jq  # append-only audit trail
```

The demo worker registers a simulated HIGH-risk `deployment.rollback` tool. Give
an agent `["metrics.read", "deployment.rollback"]` and the mission
`"max_risk": "HIGH"`, and the demo investigator will ask to roll back checkout.
A waiting execution holds no worker. Approvals expire after
`FORGE_APPROVAL_TTL_SECONDS` (default 86400).

## Durability and Temporal

Two execution backends share one contract
([`docs/decisions/0011`](docs/decisions/0011-temporal-backend-and-distributed-durability.md)).
- **PostgreSQL (default):** workers claim work directly.
- **Temporal:** `FORGE_EXECUTION_BACKEND=temporal`, started with `make up-temporal`. It runs one workflow per task attempt, with durable timers for approval expiry and wake-up signals for decisions and cancellations. The Temporal UI is at `127.0.0.1:18233`.

On both backends:
- Run as many workers as you like (`docker compose up -d --scale worker=3`).
- A killed or frozen worker's lease is taken over.
- A database or Temporal outage is waited out.
- A non-idempotent tool interrupted mid-call is never repeated.

Tests:
- `tests/integration/test_process_crashes.py` SIGKILLs real worker processes on both backends.
- `tests/integration/test_chaos.py` is a seeded chaos suite.
- `tests/e2e/test_phase9_chaos.py` runs the chaos drill against the Docker stack.

## Local services

| Service    | Host address      | Override                   |
|------------|-------------------|----------------------------|
| PostgreSQL | `127.0.0.1:15432` | `FORGE_POSTGRES_HOST_PORT` |
| Redis      | `127.0.0.1:16379` | `FORGE_REDIS_HOST_PORT`    |
| API        | `127.0.0.1:18000` | `FORGE_API_HOST_PORT`      |
| Temporal   | `127.0.0.1:17233` (UI `18233`) | `FORGE_TEMPORAL_HOST_PORT` (profile `temporal`) |

The credentials in `compose.yaml` are development defaults only.

## Configuration

| Variable | Required | Default |
|---|---|---|
| `FORGE_DATABASE_URL` | yes | — (e.g. `postgresql+psycopg://forge:forge@127.0.0.1:15432/forge`) |
| `FORGE_REDIS_URL` | yes | — (e.g. `redis://127.0.0.1:16379/0`) |
| `FORGE_LOG_LEVEL` | no | `INFO` |
| `FORGE_API_HOST` / `FORGE_API_PORT` | no | `127.0.0.1` / `8000` |
| `FORGE_READINESS_TIMEOUT_SECONDS` | no | `2.0` |
| `FORGE_OTEL_EXPORTER` | no | `none` (`console`, or `otlp` using the standard `OTEL_EXPORTER_OTLP_*` variables) |
| `FORGE_WORKER_ID` | no | `<hostname>-<pid>` |
| `FORGE_WORKER_LEASE_SECONDS` / `FORGE_WORKER_POLL_SECONDS` | no | `30` / `1` |
| `FORGE_OPERATOR_TOKENS` (API) | no | none: no one can approve, write policy, pin, or promote |
| `FORGE_APPROVAL_TTL_SECONDS` (worker) | no | `86400` |
| `FORGE_EXECUTION_BACKEND` (API and worker) | no | `postgres` (or `temporal`) |
| `FORGE_TEMPORAL_ADDRESS` / `_NAMESPACE` / `_TASK_QUEUE` | with Temporal | `127.0.0.1:7233` / `default` / `forge-tasks` |
| `FORGE_TEMPORAL_APPROVAL_POLL_SECONDS` | no | `60` (a parked task re-checks Postgres at least this often) |

The worker needs only `FORGE_DATABASE_URL`. Run the processes outside Docker with
`uv run python -m forge.api` and `uv run python -m forge.runtime`.

## Migrations

Migrations are written by hand. Autogenerate is not used.

```sh
make migration m="add missions table"   # new file in src/forge/infrastructure/migrations/versions/
make migrate                            # apply to FORGE_DATABASE_URL (defaults to the compose database)
```

## Layout

```text
src/forge/
  domain/           # domain model and persistence ports: no framework or adapter imports (enforced)
  orchestration/    # control plane: agents, missions, tasks (single writer)
  runtime/          # durable agent loop and worker (python -m forge.runtime)
  inference/        # model port, pricing, deterministic demo model
  tools/            # tool port, authorization pipeline, native tools
  security/         # egress, secrets, approvals, policy and CRITICAL writes
  evaluation/       # evaluators, benchmarks, evaluation worker
  capabilities/     # capability graph, competency, reliability, fitness
  learning/         # failure analysis, gaps, skill generation, validation, promotion
  api/              # FastAPI adapter and composition root (python -m forge.api)
  infrastructure/   # PostgreSQL unit of work, Redis, migrations
  observability/    # structured logging, OpenTelemetry tracing
  config.py         # FORGE_* environment configuration
  health.py         # dependency health-check contract
tests/
  unit/             # no external services (`make test`)
  integration/      # needs Postgres and Redis (`make test-integration`)
  e2e/              # drives the running compose stack (`make e2e`)
```
