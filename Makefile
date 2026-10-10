# Canonical development commands. CI runs these same targets.
.PHONY: install format lint typecheck boundaries test test-integration e2e e2e-temporal test-live check up up-temporal down migrate migration

# Defaults point at the services started by `make up` (see compose.yaml host ports).
FORGE_DATABASE_URL ?= postgresql+psycopg://forge:forge@127.0.0.1:15432/forge
FORGE_REDIS_URL ?= redis://127.0.0.1:16379/0
SERVICE_ENV = FORGE_DATABASE_URL=$(FORGE_DATABASE_URL) FORGE_REDIS_URL=$(FORGE_REDIS_URL)

install:  ## Install locked dependencies into .venv
	uv sync --locked

format:  ## Apply formatting and autofixable lint rules
	uv run ruff format src tests
	uv run ruff check --fix src tests

lint:  ## Lint and verify formatting
	uv run ruff check src tests
	uv run ruff format --check src tests

typecheck:  ## Strict static type checking
	uv run mypy

boundaries:  ## Enforce architectural import contracts
	uv run lint-imports

test:  ## Unit tests (no external services)
	uv run pytest

test-integration:  ## Integration tests against running PostgreSQL and Redis
	$(SERVICE_ENV) uv run pytest -m integration

e2e:  ## End-to-end demos and chaos against the running stack (`make up` first)
	uv run pytest -m e2e -s

e2e-temporal:  ## The same end-to-end suite on the Temporal backend (`make up-temporal` first)
	FORGE_EXECUTION_BACKEND=temporal uv run pytest -m e2e -s

test-live:  ## Real provider calls (needs ANTHROPIC_API_KEY and/or OPENAI_API_KEY; costs money)
	uv run pytest -m live

check: lint typecheck boundaries test  ## Everything that runs without external services

up:  ## Build and start the full stack, waiting until healthy
	docker compose up -d --build --wait

up-temporal:  ## The same stack on the Temporal execution backend (ADR 0011)
	FORGE_EXECUTION_BACKEND=temporal docker compose --profile temporal up -d --build --wait

down:  ## Stop the stack (either backend) and delete its volumes
	docker compose --profile temporal down -v

migrate:  ## Apply migrations to the database at FORGE_DATABASE_URL
	$(SERVICE_ENV) uv run alembic upgrade head

migration:  ## Create a hand-written migration: make migration m="add missions table"
	@test -n "$(m)" || (echo 'usage: make migration m="description"' && exit 1)
	uv run alembic revision -m "$(m)"
