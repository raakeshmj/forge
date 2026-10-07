# FORGE

FORGE is an autonomous agent operations and continuous-learning platform. Start with [`CLAUDE.md`](CLAUDE.md) and [`docs/`](docs/). Architecture decisions are recorded in [`docs/decisions/`](docs/decisions/).

**Status:** Phase 0 (foundation) is implemented. There is no agent functionality yet. See [`docs/IMPLEMENTATION_PLAN.md`](docs/IMPLEMENTATION_PLAN.md).

## Requirements

- [uv](https://docs.astral.sh/uv/). It installs Python 3.12 if needed.
- Docker with Compose v2

## Quickstart

```sh
make install           # create .venv from uv.lock
make check             # lint, types, import boundaries, unit tests
make up                # build and start Postgres, Redis, migrations, API; waits until healthy
make test-integration  # integration + migration tests against the running stack
curl localhost:18000/health/ready
make down              # stop and delete volumes
```

The Makefile is the source of canonical commands. CI runs the same targets.

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

Run the API outside Docker with `uv run python -m forge.api`, once the variables above are set.

## Migrations

Migrations are written by hand. Autogenerate is not used.

```sh
make migration m="add missions table"   # new file in src/forge/infrastructure/migrations/versions/
make migrate                            # apply to FORGE_DATABASE_URL (defaults to the compose database)
```

## Layout

```text
src/forge/
  domain/           # domain model: no framework, driver, or adapter imports (enforced)
  api/              # FastAPI adapter and composition root (python -m forge.api)
  infrastructure/   # PostgreSQL, Redis, migrations
  observability/    # structured logging
  config.py         # FORGE_* environment configuration
  health.py         # dependency health-check contract
tests/
  unit/             # no external services (`make test`)
  integration/      # needs Postgres and Redis (`make test-integration`)
```
