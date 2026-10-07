# Canonical development commands. CI runs these same targets.
.PHONY: install format lint typecheck test check

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

test:  ## Unit tests (no external services)
	uv run pytest

check: lint typecheck test  ## Everything that runs without external services
