# Canonical development commands. CI runs these same targets.
.PHONY: install format lint typecheck boundaries test test-integration check up down

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

check: lint typecheck boundaries test  ## Everything that runs without external services

up:  ## Build and start the full stack, waiting until healthy
	docker compose up -d --build --wait

down:  ## Stop the stack and delete its volumes
	docker compose down -v
