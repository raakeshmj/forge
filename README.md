# FORGE

FORGE is an autonomous agent operations and continuous-learning platform. Start with [`CLAUDE.md`](CLAUDE.md) and [`docs/`](docs/). Architecture decisions are recorded in [`docs/decisions/`](docs/decisions/).

**Status:** Phases 0-3 are implemented: a durable single-agent runtime with provider-aware inference, explicit context planning, provider-native prompt caching, and a secure tool runtime (registry, MCP, remote API tools). See [`docs/IMPLEMENTATION_PLAN.md`](docs/IMPLEMENTATION_PLAN.md).

## Requirements

- [uv](https://docs.astral.sh/uv/). It installs Python 3.12 if needed.
- Docker with Compose v2

## Quickstart

```sh
make install           # create .venv from uv.lock
make check             # lint, types, import boundaries, unit tests
make up                # build and start Postgres, Redis, migrations, API; waits until healthy
make test-integration  # integration, recovery, and migration tests against the running stack
make e2e               # Phase 1 demo: SIGKILLs the worker mid-mission and resumes it
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

## Local services

| Service    | Host address      | Override                   |
|------------|-------------------|----------------------------|
| PostgreSQL | `127.0.0.1:15432` | `FORGE_POSTGRES_HOST_PORT` |
| Redis      | `127.0.0.1:16379` | `FORGE_REDIS_HOST_PORT`    |
| API        | `127.0.0.1:18000` | `FORGE_API_HOST_PORT`      |

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
