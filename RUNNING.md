# Running FORGE

How to start FORGE on your machine, run a mission end to end, and use the control plane, approvals, learning, Temporal and the test suites. Every command here was run against the Docker stack on macOS.

## 1. What you need

| Tool | Why |
|---|---|
| Docker with Compose v2 | Runs Postgres, Redis, the API, workers and the UI |
| [uv](https://docs.astral.sh/uv/) | Python 3.12 environment for the tests and the CLI (`make install`) |
| `make`, `curl`, `jq`, `openssl` | The commands below |
| Node.js 22.13+ (optional) | Only to work on the UI outside Docker |

No API keys are needed. FORGE ships a deterministic local model, the `demo` provider, which everything below uses.

## 2. Start it

```sh
make install                                        # Python deps from uv.lock into .venv
export FORGE_OPERATOR_TOKENS="alice:$(openssl rand -hex 24)"   # a human operator (see §5)
make up                                             # build and start everything; waits until healthy
curl -s localhost:18000/health/ready                # {"status":"ready",...}
```

`make up` starts these services, all on loopback:

| Service | Address |
|---|---|
| API | <http://127.0.0.1:18000> (OpenAPI docs at `/docs`) |
| Control-plane UI | <http://127.0.0.1:13000> |
| PostgreSQL | `127.0.0.1:15432` (user, password and database are all `forge`) |
| Redis | `127.0.0.1:16379` |

Migrations run automatically before the API and worker start. If a port is taken, override it, for example `FORGE_API_HOST_PORT=28000 make up`. Variables `FORGE_POSTGRES_HOST_PORT`, `FORGE_REDIS_HOST_PORT` and `FORGE_UI_HOST_PORT` work the same way.

## 3. Run a mission

```sh
API=localhost:18000

# 1. Register an agent.
agent=$(curl -s $API/agents -H 'content-type: application/json' -d '{
  "name": "investigator", "role": "incident investigator",
  "instructions": "Investigate with the tools available.",
  "model": {"provider": "demo", "name": "demo-investigator-1"},
  "tools": ["metrics.read"], "capabilities": ["incident-response"]}' | jq -r .agent_id)

# 2. Give it a mission.
mission=$(curl -s $API/missions -H 'content-type: application/json' -d "{
  \"objective\": \"Why did checkout latency increase?\", \"agent_id\": \"$agent\",
  \"budget_micro_usd\": 100000}" | jq -r .id)

# 3. Wait for it to finish and be evaluated (a few seconds), then read the result, cost and cache use.
until [ "$(curl -s $API/missions/$mission/evaluations | jq length)" -gt 0 ]; do sleep 1; done
curl -s $API/missions/$mission | jq '{status, executions: [.executions[] | {status, model_name, cost_micro_usd, cache_hit_rate, output}]}'

# 4. The immutable event history, from mission.created to evaluation.completed.
curl -s $API/missions/$mission/events | jq -r '.[].event_type'

# 5. The evaluation: eight dimensions per mission.
curl -s $API/missions/$mission/evaluations | jq '.[0].results[] | select(.scope=="mission") | {dimension, score}'

# To cancel a mission that is still running:
# curl -s -X POST $API/missions/$mission/cancel
```

A mission can be planned three ways. Use exactly one of these fields:

| Field | Plan |
|---|---|
| `agent_id` | One task |
| `tasks` | An explicit graph. Each task has `key`, `depends_on` and `required_capabilities` |
| `planner_agent_id` | A planner agent proposes the graph, and FORGE validates it |

Cost is in micro-USD, so `100000` means $0.10.

## 4. Watch it in the control plane

Open <http://127.0.0.1:13000>.

The quickest way to try FORGE is **New mission** in the sidebar, or ⌘K then "new":
1. Type an objective, or click one of the examples.
2. Pick an agent. Pick a planner to have the work split into a task graph first.
3. Press **Launch mission** (or ⌘↵).

You land on the mission's live page. It updates as the mission plans, runs and is evaluated. With no agents registered, the page offers a one-click demo investigator.

- **Dashboard and Missions:** status, cost and tokens for each mission.
- **Mission detail:** the task graph, the live event stream, every model call (latency, tokens, cache, cost), tool calls, results and the evaluation.
- **Agents and Agent detail:** competency with uncertainty, reliability, failure clusters, skills, model usage and cost.
- **Capability graph, Skills, Evaluations, Benchmarks, Policies, Approvals, Observability and Costs** pages.

The UI shows no prompts, tool arguments or tool results.

## 5. Approve a high-risk action

Every tool has a risk level:

| Risk | What happens |
|---|---|
| LOW | Runs |
| MEDIUM | Runs only if a policy allows it |
| HIGH | Waits for a human to approve that exact call |
| CRITICAL | Disabled unless an operator enables it, and then also needs approval |

Humans are the operators listed in `FORGE_OPERATOR_TOKENS` (`name:token`, with tokens of at least 32 characters). The variable must be set when `make up` runs. If it is empty, nobody can approve anything, write policy, or promote skills. To add an operator later, export the variable and run `make up` again.

The demo worker includes a simulated HIGH-risk `deployment.rollback` tool:

```sh
TOKEN=${FORGE_OPERATOR_TOKENS#*:}

responder=$(curl -s $API/agents -H 'content-type: application/json' -d '{
  "name": "responder", "role": "incident responder",
  "instructions": "Investigate, then remediate.",
  "model": {"provider": "demo", "name": "demo-investigator-1"},
  "tools": ["metrics.read", "deployment.rollback"]}' | jq -r .agent_id)
incident=$(curl -s $API/missions -H 'content-type: application/json' -d "{
  \"objective\": \"Checkout latency regressed; fix it.\", \"agent_id\": \"$responder\",
  \"budget_micro_usd\": 100000, \"max_risk\": \"HIGH\"}" | jq -r .id)

# The agent investigates, then asks to roll back checkout, and the mission waits.
until [ "$(curl -s $API/missions/$incident | jq -r .status)" = WAITING_APPROVAL ]; do sleep 1; done
curl -s "$API/approvals?status=PENDING" | jq '.[] | {id, tool, risk, rationale, evidence, expires_at}'
id=$(curl -s "$API/approvals?status=PENDING" | jq -r '.[0].id')

curl -s -X POST $API/approvals/$id/decision -H "Authorization: Bearer $TOKEN" \
  -H 'content-type: application/json' -d '{"decision": "approve", "reason": "on-call approved"}' | jq -c '{status, decided_by}'
until [ "$(curl -s $API/missions/$incident | jq -r .status)" = COMPLETED ]; do sleep 1; done
curl -s "$API/audit?mission_id=$incident" | jq -r '.[] | "\(.actor) \(.action) \(.decision)"'
```

- **Deny instead:** send `"decision": "deny"`.
- **Missing or wrong token:** the request returns `401`.
- **In the UI:** use the Approvals page. Paste the token into the form; it is sent once and never stored.
- **Expiry:** approvals expire after `FORGE_APPROVAL_TTL_SECONDS` (default 86400).
- **Restarts:** a waiting mission holds no worker and survives restarts.

## 6. Benchmark, learn, promote, roll back

The demo investigator always checks checkout, so it passes 1 of the 3 cases in the built-in incident benchmark. Use the agent from §3:

```sh
TOKEN=${FORGE_OPERATOR_TOKENS#*:}

# Measure the agent against ground truth (about 5 seconds).
benchmark() {
  run=$(curl -s -X POST $API/benchmarks/incident-investigation/runs -H 'content-type: application/json' \
    -d "{\"agent_id\": \"$agent\"}" | jq -r .id)
  until [ "$(curl -s $API/benchmark-runs/$run | jq -r .status)" = COMPLETED ]; do sleep 1; done
  curl -s $API/benchmark-runs/$run | jq -c '{passed: .summary.passed, cases: .summary.cases}'
}
benchmark                                               # {"passed":1,"cases":3}

# Learn from the failures: analysis, then gap, then a candidate skill, then held-out A/B validation.
cycle=$(curl -s $API/learning/cycles -H 'content-type: application/json' \
  -d "{\"agent_id\": \"$agent\", \"capability\": \"incident-response\"}" | jq -r .id)
until curl -s $API/learning/cycles/$cycle | jq -e '.status | IN("VALIDATED", "REJECTED", "NO_GAP", "NEEDS_DATASET")' >/dev/null; do sleep 2; done
curl -s $API/learning/cycles/$cycle | jq '{status, skill, gap, learning_delta}'   # VALIDATED

# An operator promotes it, and the same agent now passes every case.
curl -s -X POST $API/learning/cycles/$cycle/promote -H "Authorization: Bearer $TOKEN" | jq -c '{status}'
benchmark                                               # {"passed":3,"cases":3}

# Undo the promotion.
curl -s -X POST $API/learning/cycles/$cycle/rollback -H "Authorization: Bearer $TOKEN" \
  -H 'content-type: application/json' -d '{"reason": "rolling back"}' | jq -c '{status}'
```

Production behaviour changes only on an explicit promotion. A single failure never changes it.

## 7. Use real models (Anthropic, OpenAI)

The worker enables a provider only when its key is present. Agents can then name `{"provider": "anthropic", "name": "claude-sonnet-5-5"}` or `{"provider": "openai", "name": "gpt-5-mini"}`; the full list is in `src/forge/inference/profiles.py`.

The Compose worker doesn't pass provider keys through by default. Create `compose.override.yaml` next to `compose.yaml` (Compose loads it automatically):

```yaml
services:
  worker:
    environment:
      ANTHROPIC_API_KEY: ${ANTHROPIC_API_KEY:-}
      OPENAI_API_KEY: ${OPENAI_API_KEY:-}
```

Then `export ANTHROPIC_API_KEY=...` and run `make up`.

- **Where keys go:** only to the worker's provider clients. They never enter model context, events or telemetry.
- **Live check:** `make test-live` runs real prompt-caching checks. It costs money.
- **Untested:** this path has not been run with real keys. Every result in this repository comes from the demo provider.

## 8. Use the Temporal backend

```sh
make down
make up-temporal              # same stack, plus a Temporal dev server; tasks run as Temporal workflows
make e2e-temporal             # the end-to-end suite on this backend
```

The Temporal UI is at <http://127.0.0.1:18233>. Postgres remains the system of record. Temporal adds durable timers and wake-up signals. The bundled server is Temporal's single-binary dev server; a production cluster needs its own TLS and auth setup.

## 9. Run more workers

```sh
docker compose up -d --scale worker=3
```

Workers coordinate through leases in Postgres. If a worker is killed or frozen, another takes over its task, and a non-idempotent tool is never re-run. `make e2e` includes a drill that kills workers and restarts Postgres.

## 10. Run processes outside Docker

Start the infrastructure with `make up`. Then, for example, stop the Compose API and worker, and run your own against the same database:

```sh
export FORGE_DATABASE_URL=postgresql+psycopg://forge:forge@127.0.0.1:15432/forge
export FORGE_REDIS_URL=redis://127.0.0.1:16379/0
FORGE_API_PORT=18001 uv run python -m forge.api      # API (needs both URLs)
uv run python -m forge.runtime                       # worker (needs FORGE_DATABASE_URL)
cd ui && npm ci && FORGE_API_URL=http://127.0.0.1:18001 npm run dev   # UI on :13000
```

Other settings (log level, OpenTelemetry exporter, lease times, Temporal address) are listed under Configuration in `README.md`.

## 11. Tests and checks

| Command | What it runs | Needs |
|---|---|---|
| `make check` | Lint, `mypy --strict`, import-boundary contracts, unit tests | Nothing |
| `make test-integration` | Integration, recovery, crash and chaos tests (some start their own Temporal dev server) | `make up` |
| `make e2e` | End-to-end demos and the chaos drill against the stack | `make up` |
| `make e2e-temporal` | The same on Temporal | `make up-temporal` |
| `make ui-install ui-check` | UI lint, types, unit tests, production build | Node 22.13+ |
| `make test-live` | Real provider calls | API keys; costs money |

The Python suites take about 2 minutes each on a laptop. Keep the machine awake and plugged in: if the host sleeps mid-run, tests appear to hang.

## 12. Benchmarks

```sh
make benchmark     # needs `make up`; about 20 seconds
```

This runs every arm (single agent, after learning, naive multi-agent, FORGE routing, naive context, no caching, and two chaos arms) on a scratch database. It writes `benchmarks/results/latest.json` and `docs/BENCHMARKS.md`. The same seed reproduces every number except wall-clock latency. To change repeats, seed or output paths, run it directly with the variables from §10 set: `uv run python -m forge.benchmarking --repeats 5 --seed 11`. It creates and drops its own scratch database and never touches yours.

## 13. Stop and reset

```sh
make down          # stops everything (either backend) and deletes all data volumes
```

To keep the data, use `docker compose stop`, then `docker compose start`.

## 14. Troubleshooting

| Symptom | Cause and fix |
|---|---|
| `make up` fails on a port | Another service is using it. Set `FORGE_*_HOST_PORT` (§2) |
| `401` on approve, promote or policy writes | No token, or a wrong one. Check that `FORGE_OPERATOR_TOKENS` was set when `make up` ran |
| A mission stays `WAITING_APPROVAL` | It is waiting for a human. Decide it in §5 or on the Approvals page |
| A mission fails with `BUDGET_EXCEEDED` | Raise `budget_micro_usd` |
| An agent naming `anthropic` or `openai` never runs | The worker has no key for that provider (§7) |
| Tests seem to hang | The machine slept, or the stack isn't up. Check `docker compose ps` |
| Logs | `docker compose logs -f api worker` |
